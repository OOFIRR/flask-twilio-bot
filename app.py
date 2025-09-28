import os
import json
import base64
import time
import logging
import io # נדרש לטיפול בבייטים של ElevenLabs

# אינטגרציה של Flask ו-WebSockets
from flask import Flask, request, Response
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from twilio.twiml.voice_response import VoiceResponse, Stream, Say, Play

# אינטגרציה של Google Cloud Speech-to-Text
from google.cloud import speech_v1p1beta1 as speech

# אינטגרציה של OpenAI ו-ElevenLabs
import openai
import requests

# --- 1. הגדרות ו-Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
app = Flask(__name__)

# הגדרת משתני סביבה
openai.api_key = os.environ.get('OPENAI_API_KEY', 'YOUR_OPENAI_API_KEY')
ELEVEN_API_KEY = os.environ.get('ELEVEN_API_KEY', 'YOUR_ELEVEN_API_KEY')
# VOICE_ID: קול עברי לדוגמה (Adam/Koren). יש להחליף ב-ID הקול המדויק שלך!
# אם לא מוגדר, נשתמש ב-ID ידוע של Adam.
VOICE_ID = os.environ.get('ELEVEN_VOICE_ID', 'EXa5x5N5kF7y5t2yJ90r') 

# הגדרות Google STT
GCP_SPEECH_CLIENT = speech.SpeechClient()
STT_STREAMING_CONFIG = speech.StreamingRecognitionConfig(
    config=speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
        sample_rate_hertz=8000,
        language_code='he-IL',
        model='default', 
    ),
    interim_results=False, 
    single_utterance=False,
)

# --- 2. פונקציות עזר לאינטגרציה ---

def get_llm_response(prompt: str) -> str:
    """שולח פרומפט ל-GPT-4o-mini ומחזיר תשובה."""
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
    """
    *** פונקציית Placeholder! ***
    Twilio דורשת URL ציבורי. בפרויקט אמיתי, קטע קוד זה מעלה את קובץ האודיו 
    (שמתקבל מ-ElevenLabs) ל-S3, Google Cloud Storage, או לשרת CDN אחר, 
    ומחזיר את ה-URL שלו.

    כיוון שאנו מוגבלים, אנו לא יכולים ליישם העלאה אמיתית כאן.
    לצורך הדגמה, אנו נחזור לפתרון ה-Say של Twilio.
    אם נממש אחסון קבצים, זו תהיה הנקודה לשלב אותו.
    """
    logging.warning("CDN Upload is not implemented. Returning placeholder URL for Twilio to use Say instead.")
    return "placeholder_url_for_say_fallback"


def synthesize_speech_url(text: str) -> str:
    """
    שולח טקסט ל-ElevenLabs, ממיר לאודיו MP3, ומחזיר URL נגיש ל-Twilio.
    """
    if not ELEVEN_API_KEY:
        logging.error("ElevenLabs API Key is missing. Falling back to Twilio Say.")
        return text # במקרה חירום, מחזיר את הטקסט שיוקרא על ידי Twilio Say
        
    logging.info(f"Synthesizing speech for: '{text[:30]}...' using ElevenLabs.")

    # הגדרות ElevenLabs API
    url = f'https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}'
    headers = {
        'xi-api-key': ELEVEN_API_KEY,
        'Content-Type': 'application/json'
    }
    payload = {
        'text': text,
        'model_id': 'eleven_multilingual_v2', # מודל רב-לשוני תומך בעברית
        'voice_settings': {
            'stability': 0.5,
            'similarity_boost': 0.8
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=payload)
        resp.raise_for_status() # זורק שגיאה אם הסטטוס אינו 2xx
        
        audio_bytes = resp.content
        
        # --- שלב העלאה ל-CDN (כרגע placeholder) ---
        # בגלל מגבלות סביבת הקוד, אנחנו נחזור ל-Say למרות הקריאה ל-ElevenLabs
        # אם קריאת ElevenLabs הצליחה, נשתמש בטקסט שהתקבל עבור Say.
        logging.warning("ElevenLabs successful, but returning text for Twilio Say (CDN not implemented).")
        return text # מחזיר טקסט כדי שיוקרא על ידי Twilio Say
        
        # # אם היה לנו CDN:
        # audio_url = upload_audio_to_cdn(audio_bytes)
        # return audio_url

    except requests.exceptions.RequestException as e:
        logging.error(f"שגיאת ElevenLabs: {e}")
        return text # במקרה של שגיאה, מחזיר טקסט כגיבוי (Fallback)

# --- 3. Webhook לניהול שיחות (Twilio Voice) ---

@app.route('/voice', methods=['POST'])
def voice_webhook():
    """מקבל שיחה נכנסת ומפנה ל-WebSocket Stream."""
    
    host = request.headers.get('Host')
    
    response = VoiceResponse()
    
    # ברכה קצרה בעברית
    response.say('שלום, אנא דברו לאחר הצליל.', language='he-IL')
    
    # פתיחת Stream לקבלת אודיו ב‑WebSocket
    ws_url = f'wss://{host}/stream'
    response.append(Stream(url=ws_url))
    
    logging.info(f"Voice Webhook initialized stream to: {ws_url}")
    return Response(str(response), mimetype='application/xml')

# --- 4. לוגיקת WebSocket לתמלול ודיאלוג ---

def generate_stt_requests(ws):
    """
    פונקציית Generator שמקבלת אודיו מה-WebSocket של Twilio
    ושולחת אותו ל-Google STT.
    """
    # ... (קוד זהה לשליחת האודיו ל-GCP)
    while True:
        try:
            message = ws.receive()
            if message is None:
                break
            
            data = json.loads(message)
            
            if data['event'] == 'media':
                audio_chunk = base64.b64decode(data['media']['payload'])
                yield audio_chunk
            
            elif data['event'] == 'stop':
                logging.info("Twilio Stream stopped.")
                break
                
        except Exception as e:
            logging.error(f"Error receiving audio from WebSocket: {e}")
            break


@app.route('/stream')
def stream():
    """נקודת קצה ל-WebSocket שמטפלת בזרם האודיו."""
    
    ws = request.environ.get('wsgi.websocket')
    if not ws:
        logging.error("Expected WebSocket request.")
        return "Expected WebSocket", 400

    logging.info("WebSocket connection established with Twilio.")
    
    audio_generator = generate_stt_requests(ws)
    
    # שליחת האודיו ל-Google STT וקבלת תמלול
    # שימוש ב-try/except חיוני כאן
    try:
        stt_responses = GCP_SPEECH_CLIENT.streaming_recognize(STT_STREAMING_CONFIG, audio_generator)
    except Exception as e:
        logging.error(f"FATAL: Google STT API connection failed. Check GCP Permissions! Error: {e}")
        # Twilio ינתק את השיחה אם אין תגובה, או שתנתק כאן במפורש.
        return "STT Error", 500

    full_transcript = []

    try:
        for response in stt_responses:
            if not response.results:
                continue

            result = response.results[0]
            
            if result.is_final:
                transcript = result.alternatives[0].transcript
                full_transcript.append(transcript)
                logging.info(f"FINAL TRANSCRIPT: {transcript}")
                
                if full_transcript:
                    final_user_input = " ".join(full_transcript)
                    
                    # 1. עיבוד LLM (OpenAI)
                    llm_response_text = get_llm_response(final_user_input)
                    
                    # 2. המרת טקסט לדיבור (ElevenLabs / Twilio Say)
                    tts_result = synthesize_speech_url(llm_response_text)
                    
                    # 3. יצירת Twilio Media Control Message להשמעת התשובה
                    twiml_response = VoiceResponse()
                    
                    # כרגע, אנחנו משתמשים ב-Twilio Say כ-fallback/placeholder
                    twiml_response.say(tts_result, language='he-IL') 
                    
                    # אם היה לנו URL אמיתי מ-CDN, היינו משתמשים בזה:
                    # if tts_result.startswith('http'):
                    #     twiml_response.play(tts_result)
                    # else:
                    #     twiml_response.say(tts_result, language='he-IL')

                    response_twiml_message = {
                        "event": "media_control",
                        "text": str(twiml_response)
                    }
                    
                    ws.send(json.dumps(response_twiml_message))
                    logging.info("Sent TwiML control message to Twilio.")

                    # איפוס לשיחה הבאה
                    full_transcript = []
                    
    except Exception as e:
        logging.error(f"General error in STT/LLM loop: {e}")
        
    finally:
        logging.info("Closing WebSocket connection.")
        # סגירת ה-WebSocket
        

# --- 5. הפעלת השרת ---

if __name__ == '__main__':
    logging.info("Starting Flask server with WebSocket handler...")
    http_server = WSGIServer(('', int(os.environ.get('PORT', 5000))), app, handler_class=WebSocketHandler)
    http_server.serve_forever()
