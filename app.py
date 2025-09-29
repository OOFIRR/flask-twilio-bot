import os
import json
import base64
import time
import logging
import io 
import traceback 

# אינטגרציה של Flask ו-WebSockets
from flask import Flask, request, Response
# ייבוא Gevent נדרש להפעלת שרת WSGI שתומך ב-WebSockets
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
PORT = int(os.environ.get('PORT', 8080)) # קבלת הפורט ממשתנה הסביבה PORT

# הגדרות Google STT
# -----------------------------------------------------------------------------
GCP_SPEECH_CLIENT = None
try:
    GCP_CREDENTIALS_JSON = os.environ.get('GCP_CREDENTIALS_JSON')
    if not GCP_CREDENTIALS_JSON:
        logging.error("GCP_CREDENTIALS_JSON environment variable is not set. STT will fail.")
    else:
        GCP_CREDENTIALS_INFO = json.loads(GCP_CREDENTIALS_JSON)
        GCP_CREDENTIALS = service_account.Credentials.from_service_account_info(GCP_CREDENTIALS_INFO)
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
        # השארתי את הקוד כך שיעבוד עם OpenAI API 
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

def synthesize_speech_url(text: str) -> str:
    if not ELEVEN_API_KEY:
        logging.error("ElevenLabs API Key is missing. Falling back to Twilio Say.")
        return text 
        
    # אין צורך להריץ את ElevenLabs כרגע כיוון שאנחנו משתמשים ב-Say
    logging.warning("ElevenLabs successful, but returning text for Twilio Say (CDN not implemented).")
    return text 

# --- 3. Webhook לניהול שיחות (Twilio Voice) ---

@app.route('/voice', methods=['POST'])
def voice_webhook():
    # Railway משתמש ב-Host שכולל את הדומיין
    host = request.headers.get('Host') 
    
    response = VoiceResponse()
    
    response.say('שלום, אנא דברו לאחר הצליל.', language='he-IL') 
    
    with response.start() as start:
        # חשוב מאוד ש-Twilio ישתמש ב-wss://host/stream
        ws_url = f'wss://{host}/stream'
        start.stream(url=ws_url, track='inbound')
        
    logging.info(f"Voice Webhook initialized stream to: {ws_url}. Session SID: {request.form.get('CallSid')}")
    return Response(str(response), mimetype='application/xml')

# --- 4. לוגיקת WebSocket לתמלול ודיאלוג ---

def generate_stt_requests(ws):
    """ גנרטור שמקבל הודעות מ-WebSocket ומחזיר אודיו גולמי עבור Google STT."""
    
    yield speech.StreamingRecognizeRequest(streaming_config=STT_STREAMING_CONFIG)
    logging.info("STT: Sent initial configuration request.")
    
    while True:
        try:
            # שימוש ב-ws.receive() מקבל את ההודעה הבאה מה-WebSocket 
            message = ws.receive()
            if message is None:
                logging.info("STT: WebSocket received None message (connection closed by client).")
                break
            
            data = json.loads(message)
            
            if data['event'] == 'start':
                logging.info(f"STT: Twilio Stream started. Media Stream ID: {data.get('streamSid')}.")
                continue
                
            if data['event'] == 'media':
                audio_chunk = base64.b64decode(data['media']['payload'])
                yield speech.StreamingRecognizeRequest(audio_content=audio_chunk) 
                
            elif data['event'] == 'stop':
                logging.info("STT: Twilio Stream stopped.")
                break
                
        except Exception as e:
            logging.error(f"STT: Error receiving/parsing audio from WebSocket: {e}")
            logging.error(traceback.format_exc()) 
            break


@app.route('/stream')
def stream():
    """נקודת קצה ל-WebSocket שמטפלת בזרם האודיו."""
    
    ws = request.environ.get('wsgi.websocket')
    
    if not ws:
        logging.error("STREAM ERROR: wsgi.websocket not found. Twilio cannot connect.")
        return "WebSocket Setup Failed", 400

    logging.info("STREAM: WebSocket connection established with Twilio. Ready to process STT.")
    
    if not GCP_SPEECH_CLIENT:
        logging.error("STREAM: GCP Speech Client failed to initialize. Closing stream immediately.")
        ws.close()
        return "GCP Init Error", 500
        
    audio_generator = generate_stt_requests(ws)
    full_transcript = []
    
    try:
        # ** ביצוע התמלול הזרמתית (Streaming Recognition) **
        stt_responses = GCP_SPEECH_CLIENT.streaming_recognize(
            requests=audio_generator, 
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
                
                # סיימנו לקבל את התמלול, כעת מעבדים את התגובה
                if full_transcript:
                    final_user_input = " ".join(full_transcript)
                    
                    # 1. עיבוד LLM (OpenAI)
                    llm_response_text = get_llm_response(final_user_input)
                    
                    # 2. המרת טקסט לדיבור (כעת רק מחזיר טקסט)
                    tts_result = synthesize_speech_url(llm_response_text)
                    
                    # 3. שליחת תגובה בחזרה ל-Twilio באמצעות WebSocket
                    # Twilio מצפה ל-TwiML <Say> או <Play> כדי להשמיע את התגובה
                    # מכיוון שאנחנו בתוך WebSocket, אנחנו שולחים <Say> כ-Mark
                    
                    # הערה חשובה: בשימוש עם <Start><Stream> (כמו במקרה שלנו), הזרם הוא רק נכנס (inbound)
                    # אין אפשרות מובנית לשלוח פקודת <Say> בחזרה דרך ה-WebSocket
                    # הדרך היחידה לסיים שיחת דו-שיח היא לסגור את ה-WebSocket, ולגרום ל-Twilio לחזור ל-TwiML הבא
                    
                    logging.warning(f"CONTROL: Closing WebSocket to signal end of turn. Response text: {tts_result}")
                    
                    # סגירת החיבור היא הפקודה היחידה שיש לנו כרגע כדי לסיים את תור המשתמש
                    # ברגע שה-WebSocket נסגר, Twilio ינתק את השיחה מכיוון שאין TwiML נוסף אחריה.
                    ws.close()
                    return "Stream closed after LLM response", 200

    except gcp_exceptions.GoogleAPICallError as e:
        logging.error(f"STREAM ERROR: Google API Call Error (gRPC failure). Details: {e.details}")
        logging.error(traceback.format_exc())
    except Exception as e:
        logging.error(f"STREAM ERROR: General error in STT/LLM loop or streaming_recognize: {e}")
        logging.error(traceback.format_exc()) 
        
    finally:
        logging.info("STREAM: Ensuring WebSocket connection is closed.")
        try:
            ws.close()
        except Exception as close_err:
            logging.error(f"STREAM: Error closing WebSocket: {close_err}")
            
    return "Stream handling finished", 200

# --- 5. הפעלת השרת באמצעות Gevent (עקיפת Gunicorn) ---

if __name__ == '__main__':
    logging.info(f"Starting Gevent WSGI server on port {PORT}")
    try:
        # הפעלת שרת WSGI של Gevent שתומך ב-WebSocket
        # (WebSocketHandler הוא הקריטי לתמיכה בנקודת הקצה /stream)
        http_server = WSGIServer(('', PORT), app, handler_class=WebSocketHandler)
        http_server.serve_forever()
    except Exception as e:
        logging.critical(f"FATAL: Gevent server failed to start. Error: {e}")
