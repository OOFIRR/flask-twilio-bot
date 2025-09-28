# -*- coding: utf-8 -*-
import os
import time
import requests
import json
import base64
from twilio.twiml.voice_response import VoiceResponse, Connect
from twilio.rest import Client
from flask import Flask, request, jsonify
from flask_sock import Sock
from openai import OpenAI
from google.cloud import speech
# ייבוא נדרש לאימות מול גוגל עם תוכן JSON
from google.oauth2.service_account import Credentials 
import tempfile
import json 
import logging

# הגדרת רמת לוגינג
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- הגדרות סביבה ואימות ---
# מפתחות Twilio
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
# כתובת ה-WebSocket של השרת הציבורי (מוגדרת כמשתנה סביבה ב-Railway)
WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL") # אין צורך בברירת מחדל, אנחנו בודקים למטה

# מפתחות LLM ו-TTS
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVyGqjNst4aUK-sQzS") 
# משתנה סביבה חדש/מתוקן לאימות גוגל
GCP_CREDENTIALS_JSON = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")

# קוד השפה העברי
HEBREW_LANGUAGE_CODE_TWIML = 'iw-IL'
HEBREW_LANGUAGE_CODE_GCP = 'he-IL'

# --- יצירת אובייקט Credentials גלובלי והכנה לאתחול לקוח STT ---
# נשמור את הנתיב לקובץ הזמני
GCP_CREDENTIALS_FILE_PATH = None

if GCP_CREDENTIALS_JSON:
    try:
        # 1. ודא שהתוכן הוא JSON תקין והסר רווחים/תווים מיותרים
        credentials_dict = json.loads(GCP_CREDENTIALS_JSON)
        # 2. כתיבת ה-JSON (בפורמט נקי) לקובץ זמני
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp_file:
            # שימוש ב-json.dump מבטיח כתיבה תקינה של המבנה
            json.dump(credentials_dict, temp_file)
            GCP_CREDENTIALS_FILE_PATH = temp_file.name
        logger.info(f"Google Cloud Credentials successfully processed and saved to temporary file: {GCP_CREDENTIALS_FILE_PATH}")
        
    except json.JSONDecodeError as e:
        logger.error(f"ERROR: Failed to parse GCP Credentials JSON. Check syntax and ensure no extra spaces/chars: {e}")
        GCP_CREDENTIALS_FILE_PATH = None
    except Exception as e:
        logger.error(f"ERROR: Failed to create temporary GCP Credentials file: {e}")
        GCP_CREDENTIALS_FILE_PATH = None

# אתחול הלקוחות
try:
    TWILIO_CLIENT = Client(ACCOUNT_SID, AUTH_TOKEN)
except Exception as e:
    logger.error(f"Twilio Client Initialization Failed: {e}")
    TWILIO_CLIENT = None

try:
    OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logger.error(f"OpenAI Client Initialization Failed: {e}")
    OPENAI_CLIENT = None

# אתחול Flask ו-WebSocket
app = Flask(__name__)
sock = Sock(app)

# --- גלובליים לניהול שיחות ---
CALL_CONTEXT = {}

# --- פונקציות עזר TTS והשמעה ---

def generate_and_host_elevenlabs_audio(text, call_sid, voice_id=ELEVENLABS_VOICE_ID):
    """
    יוצר אודיו באמצעות ElevenLabs ושולח פקודת Play באמצעות Twilio REST API.
    (כרגע נופל ל-Twilio Say כי אין שירות אחסון ציבורי.)
    """
    logger.info("Attempting to generate ElevenLabs TTS...")
    if not ELEVENLABS_API_KEY:
        logger.info("ElevenLabs API KEY is missing. Falling back to Twilio TTS.")
        return False
    
    # [קוד ElevenLabs נשאר זהה - נכשל בכוונה כי אין S3]
    try:
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }
        data = {
            "text": text,
            "model_id": "eleven_multilingual_v2", 
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            # שלב קריטי חסר: העלאת audio_content ל-URL ציבורי 
            logger.info("ElevenLabs audio generated successfully but cannot be hosted publicly. Falling back to Twilio TTS.")
            return False # חוזר ל-Fallback של Twilio TTS
        else:
            logger.error(f"ElevenLabs API error: {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error during ElevenLabs processing: {e}")
        return False

def call_llm_api(prompt, call_sid):
    """קריאה ל-LLM של OpenAI (או אחר) עם ההקשר של השיחה."""
    logger.info(f"Calling LLM with prompt: {prompt}")
    
    if not OPENAI_CLIENT:
        # הודעה זו תגיע מהשגיאה שהצגנו בלוגים (401)
        return "אני מצטער, מפתח OpenAI אינו מוגדר או אינו תקף. לא ניתן להשיב."

    history = CALL_CONTEXT.get(call_sid, [])
    history.append({"role": "user", "content": prompt})

    try:
        response = OPENAI_CLIENT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "אתה בוט קולי בעברית שמשיב בקצרה, חום, וטבעי. השתמש בטון דיבור קליל וידידותי."},
            ] + history,
            temperature=0.7
        )
        
        llm_response = response.choices[0].message.content
        history.append({"role": "assistant", "content": llm_response})
        CALL_CONTEXT[call_sid] = history
        return llm_response

    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        # כאן ה-Error 401 ייתפס ויחזיר את ההודעה הכללית הזו
        return "אירעה שגיאה בחיבור לשירות הבינה המלאכותית."

# --- פונקציות STT של גוגל ---

def transcribe_audio_from_chunks(audio_chunks: list) -> str:
    """
    מבנה פונקציית תמלול אמיתית באמצעות Google Cloud Speech-to-Text.
    """
    logger.info("Starting Google Cloud STT Mock/Processing...")
    
    # 1. יצירת האודיו הגולמי מכל החלקים
    audio_content = b''.join(chunk for chunk in audio_chunks if chunk) 

    if not audio_content:
        return ""
    
    # --- אתחול לקוח STT עם Credentials ---
    if not GCP_CREDENTIALS_FILE_PATH:
        # --- פתרון דמה זמני כיוון שאין אימות GCP ---
        logger.warning("Google Cloud STT Client cannot be initialized. Using STT Mock.")
        mock_response = "בבקשה תסביר לי איך המערכת הזו מתממשקת עם Twilio?"
        return mock_response
    
    try:
        # שימוש בקובץ הזמני לצורך אתחול הלקוח
        stt_client = speech.SpeechClient.from_service_account_json(GCP_CREDENTIALS_FILE_PATH)
        
        # 2. הגדרת הקונפיגורציה של STT
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=8000,  # Twilio משתמשת ב-8000Hz לשיחות טלפון
            language_code=HEBREW_LANGUAGE_CODE_GCP,
            model='default',
        )
        
        audio = speech.RecognitionAudio(content=audio_content)

        # 3. קריאה ל-API (פענוח ארוך, לא Streaming)
        response = stt_client.recognize(config=config, audio=audio)

        if response.results:
            transcription = response.results[0].alternatives[0].transcript
            logger.info(f"Google STT Result: {transcription}")
            return transcription
        
        logger.info("Google STT returned no transcription result.")
        return ""

    except Exception as e:
        # אם יש שגיאה בחיבור או בהרשאות (כמו 401), זה ייתפס כאן
        logger.error(f"Google Cloud STT ERROR (Check Permissions/API Enablement!): {e}")
        # אם יש שגיאה, נחזור ל-Mock כדי לא לגרום לקריסה
        mock_response = "אירעה שגיאה בשירות התמלול, אני אשתמש בטקסט חלופי כרגע."
        return mock_response
        


def process_audio(call_sid, audio_chunks):
    """מעבד את נתוני האודיו שנאספו, מתמלל ומשיב."""
    
    # 1. תמלול האודיו
    transcribed_text = transcribe_audio_from_chunks(audio_chunks)
    
    if not transcribed_text:
        llm_response = "לא שמעתי אותך. בבקשה נסה לדבר שוב."
    else:
        # 2. קריאה ל-LLM עם הטקסט המתומלל
        llm_response = call_llm_api(transcribed_text, call_sid)

    # 3. יצירת אודיו לתשובה (TTS)
    is_elevenlabs_used = generate_and_host_elevenlabs_audio(llm_response, call_sid)

    # 4. שליחת תגובה חזרה לשיחה
    if not is_elevenlabs_used:
        # שימוש ב-Twilio TTS (Google Standard) כ-Fallback
        logger.info(f"Responding via Twilio Say: {llm_response}")
        try:
            twiml_response = VoiceResponse()
            twiml_response.say(
                llm_response, 
                language=HEBREW_LANGUAGE_CODE_TWIML, 
                voice='Google.he-IL-Standard-A'
            )
            # לאחר ההקראה, נחבר מחדש ל-Stream כדי להמשיך להאזין
            connect = twiml_response.connect()
            connect.stream(name='Hebrew_STT', url=WEBSOCKET_URL)
            
            # שליחת TwiML לשיחה החיה
            TWILIO_CLIENT.calls(call_sid).update(twiml=str(twiml_response))
            logger.info("TwiML response sent successfully to Twilio.")
            
        except Exception as e:
            logger.error(f"Failed to send Twiml to active call {call_sid}: HTTP 400 error: {e}")
            # ניתן להשלים את השיחה כדי למנוע לולאה אינסופית
            # TWILIO_CLIENT.calls(call_sid).update(status='completed')


# --- נתיבי Webhook ו-WebSocket ---

@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """נקודת כניסה: Twilio Webhook שמתחיל את הזרמת המדיה."""
    
    # ודא שה-WEBSOCKET_URL מוגדר
    if not WEBSOCKET_URL:
        logger.critical("CRITICAL ERROR: WEBSOCKET_URL not configured. Using fallback message.")
        resp = VoiceResponse()
        resp.say("מצטערים, המערכת אינה מוגדרת כרגע. בבקשה הגדר את משתנה הסביבה W S S Web Socket URL.", language=HEBREW_LANGUAGE_CODE_TWIML)
        return str(resp)

    # בדיקה נוספת למקרה שה-URL לא נראה כמו WebSocket
    if not WEBSOCKET_URL.startswith('wss://'):
         logger.critical(f"CRITICAL ERROR: WEBSOCKET_URL has incorrect protocol: {WEBSOCKET_URL}. Must be wss://")
         resp = VoiceResponse()
         resp.say("שגיאה בהגדרת הפרוטוקול, בבקשה הגדר את הכתובת להתחלת W S S", language=HEBREW_LANGUAGE_CODE_TWIML)
         return str(resp)

    resp = VoiceResponse()

    # הודעת פתיחה
    resp.say(
        "שלום וברוך הבא, אני העוזר הקולי שלך. המערכת עוברת כעת למצב זיהוי דיבור. בבקשה דבר לאחר הצפצוף.",
        language=HEBREW_LANGUAGE_CODE_TWIML,
        voice='Google.he-IL-Standard-A'
    )
    
    # הוספת צפצוף מפורש כדי לסמן את תחילת ההקלטה
    resp.play(digits='beep')

    # חיבור ל-WebSocket
    connect = resp.connect()
    connect.stream(name='Hebrew_STT', url=WEBSOCKET_URL)
    
    return str(resp)

@sock.route('/ws')
def ws_stream(ws):
    """נקודת הקצה של ה-WebSocket שמקבלת את זרם האודיו מ-Twilio."""
    # [קוד ה-WebSocket נשאר זהה]
    logger.info("WebSocket connection established.")
    
    call_sid = None
    media_data_chunks = []
    
    try:
        while True:
            message = ws.receive()
            if message is None:
                break
            
            data = json.loads(message)
            event = data.get('event')

            if event == 'start':
                call_sid = data['start']['callSid']
                logger.info(f"Media Stream Started for Call SID: {call_sid}")
            
            elif event == 'media':
                base64_payload = data['media']['payload']
                audio_chunk = base64.b64decode(base64_payload)
                media_data_chunks.append(audio_chunk)
            
            elif event == 'stop':
                logger.info(f"Media Stream Stopped for Call SID: {call_sid}. Total chunks: {len(media_data_chunks)}")
                
                if call_sid and media_data_chunks:
                    process_audio(call_sid, media_data_chunks)
                
                media_data_chunks = []
                break

    except Exception as e:
        logger.error(f"WebSocket Error: {e}")
        
    finally:
        logger.info(f"WebSocket connection closed for Call SID: {call_sid}")

if __name__ == "__main__":
    # שינוי הפורט ל-5000 כברירת מחדל או לפי המשתנה ב-Railway
    port = int(os.environ.get("PORT", 5000))
    # app.run(host="0.0.0.0", port=port, debug=True)
    logger.info(f"Starting Flask app on port {port}...")
