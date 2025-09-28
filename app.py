import os
import json
import base64
import time
import logging
import io 
import traceback 

# אינטגרציה של Flask ו-WebSockets
from flask import Flask, request, Response
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from twilio.twiml.voice_response import VoiceResponse, Stream, Say, Play, Start
from twilio.twiml.voice_response import Stop 

# אינטגרציה של Google Cloud Speech-to-Text
from google.cloud import speech_v1p1beta1 as speech
from google.oauth2 import service_account 
from google.api_core import exceptions as gcp_exceptions 

# אינטגרציה של OpenAI ו-ElevenLabs
import openai
import requests

# --- 1. הגדרות ו-Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# הגדרת משתני סביבה
openai.api_key = os.environ.get('OPENAI_API_KEY', 'YOUR_OPENAI_API_KEY')
ELEVEN_API_KEY = os.environ.get('ELEVEN_API_KEY', 'YOUR_ELEVEN_API_KEY')
VOICE_ID = os.environ.get('ELEVEN_VOICE_ID', 'EXa5x5N5kF7y5t2yJ90r') 

# הגדרות Google STT
# -----------------------------------------------------------------------------
GCP_SPEECH_CLIENT = None
try:
    # 1. קריאת מפתח השירות המלא (JSON) ממשתנה סביבה
    GCP_CREDENTIALS_JSON = os.environ.get('GCP_CREDENTIALS_JSON')
    if not GCP_CREDENTIALS_JSON:
        logging.error("GCP_CREDENTIALS_JSON environment variable is not set. STT will fail.")
    else:
        # 2. טעינת ה-JSON והכנת האובייקט Credentials
        GCP_CREDENTIALS_INFO = json.loads(GCP_CREDENTIALS_JSON)
        GCP_CREDENTIALS = service_account.Credentials.from_service_account_info(GCP_CREDENTIALS_INFO)
        
        # 3. יצירת הלקוח באמצעות ה-Credentials שנוצרו במפורש
        GCP_SPEECH_CLIENT = speech.SpeechClient(credentials=GCP_CREDENTIALS)
        logging.info("Google Speech Client initialized successfully using JSON credentials.")

except Exception as e:
    logging.error(f"FATAL: Failed to initialize Google Speech Client during JSON parsing/initialization. Error: {e}")
    GCP_SPEECH_CLIENT = None 

# -----------------------------------------------------------------------------
STT_STREAMING_CONFIG = speech.StreamingRecognitionConfig(
    config=speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
        sample_rate_hertz=8000,
        language_code='he-IL',
        model='phone_call',
    ),
    interim_results=True,
    single_utterance=False,
)

# --- 2. פונקציות עזר לאינטגרציה ---

def get_llm_response(prompt: str) -> str:
    logging.info(f"LLM Prompt: {prompt}")
    
    messages = [
        {"role": "system", "content": "אתה יועץ שירות לקוחות בעברית. ענה בקצרה, ישירות ובצורה ידידותית. הגבל את עצמך ל-3 משפטים מקסימום."},
        {"role": "user", "content": prompt}
    ]
    
    try:
        result = openai.ChatCompletion.create(
            model='gpt-4o-mini',
            messages=messages,
            max_tokens=200,
            temperature=0.6,
        )
        response_text = result.choices[0].message.content.strip()
        logging.info(f"LLM Response: {response_text}")
        return response_text
    except Exception as e:
        logging.error(f"שגיאת OpenAI: {e}")
        return "אני מצטער, חלה שגיאה בשרת הדיאלוג."

def upload_audio_to_cdn(audio_bytes: bytes) -> str:
    # הערה: יש צורך להטמיע העלאה ל-S3/CDN אמיתי כדי להשתמש ב-<Play>
    logging.warning("CDN Upload is not implemented. Returning placeholder URL for Twilio to use Say instead.")
    return "placeholder_url_for_say_fallback"


def synthesize_speech_url(text: str) -> str:
    if not ELEVEN_API_KEY:
        logging.error("ElevenLabs API Key is missing. Falling back to Twilio Say.")
        return text 
        
    logging.info(f"Synthesizing speech for: '{text[:30]}...' using ElevenLabs.")

    # הגדרות ElevenLabs API
    url = f'https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}'
    headers = {
        'xi-api-key': ELEVEN_API_KEY,
        'Content-Type': 'application/json'
    }
    payload = {
        'text': text,
        'model_id': 'eleven_multilingual_v2', 
        'voice_settings': {
            'stability': 0.5,
            'similarity_boost': 0.8
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status() 
        
        audio_bytes = resp.content
        
        # כרגע, אנחנו לא יכולים להשתמש באודיו הזה ישירות בלי CDN
        logging.warning("ElevenLabs successful, but returning text for Twilio Say (CDN not implemented).")
        return text 

    except requests.exceptions.RequestException as e:
        logging.error(f"שגיאת ElevenLabs: {e}")
        return text 

# --- 3. Webhook לניהול שיחות (Twilio Voice) ---

@app.route('/voice', methods=['POST'])
def voice_webhook():
    host = request.headers.get('Host')
    
    response = VoiceResponse()
    
    response.say('שלום, אנא דברו לאחר הצליל.', language='he-IL') 
    
    # ** תיקון קריטי: עטיפת Stream בתוך Start **
    # הערה: כדי להמשיך את השיחה אחרי סיום ה-Stream, נדרש להוסיף action ל-Start
    # לדוגמה: with response.start(action=f'https://{host}/respond_after_stream') as start:
    with response.start() as start:
        ws_url = f'wss://{host}/stream'
        start.stream(url=ws_url, track='inbound')
        
    logging.info(f"Voice Webhook initialized stream to: {ws_url}. Session SID: {request.form.get('CallSid')}")
    return Response(str(response), mimetype='application/xml')

# --- 4. לוגיקת WebSocket לתמלול ודיאלוג ---

def generate_stt_requests(ws):
    """ גנרטור שמקבל הודעות מ-WebSocket ומחזיר אודיו גולמי עבור Google STT."""
    
    # 1. שליחת קונפיגורציה ראשונית ל-Google STT
    yield speech.StreamingRecognizeRequest(streaming_config=STT_STREAMING_CONFIG)
    logging.info("STT: Sent initial configuration request.")
    
    while True:
        try:
            message = ws.receive()
            if message is None:
                logging.info("STT: WebSocket received None message (connection closed).")
                break
            
            data = json.loads(message)
            
            if data['event'] == 'start':
                logging.info(f"STT: Twilio Stream started. Media Stream ID: {data.get('streamSid')}.")
                continue
                
            if data['event'] == 'media':
                audio_chunk = base64.b64decode(data['media']['payload'])
                # 2. שליחת נתוני האודיו ל-Google
                yield speech.StreamingRecognizeRequest(audio_content=audio_chunk) 
                
            elif data['event'] == 'stop':
                logging.info("STT: Twilio Stream stopped.")
                break
                
        except Exception as e:
            # לכידת שגיאה בלולאת קבלת האודיו (למשל, JSON פגום)
            logging.error(f"STT: Error receiving/parsing audio from WebSocket: {e}")
            logging.error(traceback.format_exc()) 
            break


@app.route('/stream')
def stream():
    """נקודת קצה ל-WebSocket שמטפלת בזרם האודיו."""
    
    logging.info("STREAM INIT: /stream endpoint hit.") 
    
    ws = request.environ.get('wsgi.websocket')
    
    if not ws:
        logging.error("STREAM ERROR: wsgi.websocket not found. Check server configuration (Gunicorn/Gevent).")
        for key, value in request.environ.items():
            if 'websocket' in key.lower() or 'upgrade' in key.lower():
                 logging.error(f"STREAM DEBUG: Found relevant environ key: {key} = {value}")
        
        return "WebSocket Setup Failed", 400

    logging.info("STREAM: WebSocket connection established with Twilio. Ready to process STT.")
    
    if not GCP_SPEECH_CLIENT:
        logging.error("STREAM: GCP Speech Client failed to initialize. Closing stream immediately.")
        ws.close()
        return "GCP Init Error", 500
        
    audio_generator = generate_stt_requests(ws)
    full_transcript = []
    
    try:
        # ** תיקון קריטי: שינוי חתימת הקריאה ל-Google STT **
        stt_responses = GCP_SPEECH_CLIENT.streaming_recognize(
            config=STT_STREAMING_CONFIG,
            requests=audio_generator, # תיקון: מעביר את הגנרטור כ-requests
            timeout=300
        )
        
        for response in stt_responses:
            if not response.results:
                continue

            result = response.results[0]
            transcript = result.alternatives[0].transcript
            
            if result.is_final:
                full_transcript.append(transcript)
                logging.info(f"TRANSCRIPT: FINAL: {transcript}")
                
                if full_transcript:
                    final_user_input = " ".join(full_transcript)
                    
                    # 1. עיבוד LLM (OpenAI)
                    llm_response_text = get_llm_response(final_user_input)
                    
                    # 2. המרת טקסט לדיבור
                    # (כרגע רק מחזיר את הטקסט, ללא ElevenLabs/CDN)
                    tts_result = synthesize_speech_url(llm_response_text)
                    
                    # 3. סגירת ה-WebSocket כדי לאפשר ל-Twilio להגיב
                    # Twilio לא מפרש TwiML דרך media_control.
                    # כרגע, אנחנו פשוט סוגרים את השיחה לאחר קבלת התשובה.
                    
                    logging.warning(f"CONTROL: Closing WebSocket to signal end of turn. Response text: {tts_result}")
                    
                    # ניתן לשלוח הודעת stop מפורשת ל-Twilio לפני סגירה:
                    # ws.send(json.dumps({"event": "stop"})) 
                    
                    # סגירת החיבור תנתק את השיחה עד שנוסיף לוגיקת action Webhook מורכבת
                    ws.close()
                    return "Stream closed after LLM response", 200

            else:
                logging.debug(f"TRANSCRIPT: INTERIM: {transcript}")
                    
    except gcp_exceptions.GoogleAPICallError as e:
        logging.error(f"STREAM ERROR: Google API Call Error (gRPC failure). Details: {e.details}")
        logging.error(traceback.format_exc())
    except Exception as e:
        logging.error(f"STREAM ERROR: General error in STT/LLM loop or streaming_recognize: {e}")
        logging.error(traceback.format_exc()) 
        
    finally:
        logging.info("STREAM: Ensuring WebSocket connection is closed.")
        try:
            # אם החיבור נסגר כבר למעלה, זה לא יקרה שוב
            ws.close()
        except Exception as close_err:
            logging.error(f"STREAM: Error closing WebSocket: {close_err}")
            
    return "Stream handling finished", 200

# --- 5. הפעלת השרת ---

if __name__ == '__main__':
    logging.info("Starting Flask server with WebSocket handler...")
    host_port = ('0.0.0.0', int(os.environ.get('PORT', 5000)))
    http_server = WSGIServer(host_port, app, handler_class=WebSocketHandler)
    http_server.serve_forever()
