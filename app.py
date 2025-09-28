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

# --- הגדרות סביבה ואימות ---
# מפתחות Twilio
ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
# כתובת ה-WebSocket של השרת הציבורי (מוגדרת כמשתנה סביבה ב-Railway)
WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL", "wss://example.up.railway.app/ws")

# מפתחות LLM ו-TTS
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVyGqjNst4aUK-sQzS") # קול עברי
# קוד השפה העברי
HEBREW_LANGUAGE_CODE_TWIML = 'iw-IL'
HEBREW_LANGUAGE_CODE_GCP = 'he-IL'

# אתחול הלקוחות
try:
    TWILIO_CLIENT = Client(ACCOUNT_SID, AUTH_TOKEN)
except Exception as e:
    print(f"Twilio Client Initialization Failed: {e}")
    TWILIO_CLIENT = None

try:
    OPENAI_CLIENT = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    print(f"OpenAI Client Initialization Failed: {e}")
    OPENAI_CLIENT = None

# אתחול Flask ו-WebSocket
app = Flask(__name__)
sock = Sock(app)

# --- גלובליים לניהול שיחות (לדוגמה פשוטה - במערכת אמיתית נשתמש ב-Redis/DB) ---
CALL_CONTEXT = {}

# --- פונקציות עזר TTS והשמעה ---

def generate_and_host_elevenlabs_audio(text, call_sid, voice_id=ELEVENLABS_VOICE_ID):
    """
    יוצר אודיו באמצעות ElevenLabs ושולח פקודת Play באמצעות Twilio REST API.
    
    שים לב: Twilio אינה יכולה לנגן Base64 או אודיו מקומי.
    ביישום אמיתי, צריך להעלות את קובץ האודיו (MP3) לשירות אחסון ציבורי (כמו AWS S3)
    ולאחר מכן להחזיר את ה-URL שלו. מכיוון שאין לנו S3, נשתמש בפתרון ה-TTS המובנה של Twilio
    (Play TwiML Bin או Google TTS).
    """
    print("Attempting to generate ElevenLabs TTS...")
    if not ELEVENLABS_API_KEY:
        print("ERROR: ELEVENLABS_API_KEY is missing. Falling back to Twilio TTS.")
        return False
    
    # מכיוון שאין לנו שירות אחסון ציבורי: נדגים רק את יצירת האודיו
    try:
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY
        }
        data = {
            "text": text,
            "model_id": "eleven_multilingual_v2", # מודל שמתאים לעברית
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            audio_content = response.content # מכיל את נתוני ה-MP3
            
            # שלב קריטי חסר: העלאת audio_content ל-URL ציבורי (כגון S3)
            # URL_PUBLICO = upload_to_s3(audio_content)
            
            # כיוון שאין אחסון, נחזיר False ונפנה ל-Twilio Say
            print("ElevenLabs audio generated successfully but cannot be hosted publicly. Falling back to Twilio TTS.")
            return False
        else:
            print(f"ElevenLabs API error: {response.text}")
            return False
            
    except Exception as e:
        print(f"Error during ElevenLabs processing: {e}")
        return False

def call_llm_api(prompt, call_sid):
    """קריאה ל-LLM של OpenAI (או אחר) עם ההקשר של השיחה."""
    print(f"Calling LLM with prompt: {prompt}")
    
    if not OPENAI_API_KEY:
        return "אני מצטער, מפתח OpenAI אינו מוגדר. לא ניתן להשיב."

    # שימוש בזיכרון שיחה גלובלי (פשטני)
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
        print(f"OpenAI API error: {e}")
        return "אירעה שגיאה בחיבור לשירות הבינה המלאכותית."

# --- פונקציות STT של גוגל ---

def transcribe_audio_from_chunks(audio_chunks: list) -> str:
    """
    מבנה פונקציית תמלול אמיתית באמצעות Google Cloud Speech-to-Text.
    """
    print("Starting Google Cloud STT Mock/Processing...")
    
    # 1. יצירת האודיו הגולמי מכל החלקים
    # (האזנה ל-chunk אחר chunk היא לוגיקה מורכבת יותר של Streaming)
    # נתונים גולמיים בפורמט L16 (PCM)
    audio_content = b''.join(chunk for chunk in audio_chunks if chunk) 

    if not audio_content:
        return ""

    # 2. הגדרת הקונפיגורציה של STT
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=8000,  # Twilio משתמשת ב-8000Hz לשיחות טלפון
        language_code=HEBREW_LANGUAGE_CODE_GCP,
        model='default', # או 'phone_call'
    )
    
    audio = speech.RecognitionAudio(content=audio_content)

    try:
        # פתרון פשוט: קריאת REST API קצרה (לא streaming)
        stt_client = speech.SpeechClient()
        response = stt_client.recognize(config=config, audio=audio)

        if response.results:
            transcription = response.results[0].alternatives[0].transcript
            print(f"Google STT Result: {transcription}")
            return transcription
        
        print("Google STT returned no transcription result.")
        return ""

    except Exception as e:
        print(f"Google Cloud STT ERROR (Check GOOGLE_APPLICATION_CREDENTIALS!): {e}")
        # --- פתרון דמה זמני כיוון שאין אימות GCP ---
        print("Using STT Mock due to GCP error.")
        mock_response = "בבקשה תסביר לי איך המערכת הזו מתממשקת עם Twilio?"
        return mock_response
        # ---------------------------------------------


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
    # ניסיון להשתמש ב-ElevenLabs
    # אם ElevenLabs נכשלת או לא מוגדרת, הפונקציה מחזירה False
    is_elevenlabs_used = generate_and_host_elevenlabs_audio(llm_response, call_sid)

    # 4. שליחת תגובה חזרה לשיחה
    if not is_elevenlabs_used:
        # שימוש ב-Twilio TTS (Google Standard) כ-Fallback
        print(f"Responding via Twilio Say: {llm_response}")
        try:
            # Twilio Say מאפשרת לשלוח TwiML באמצעות REST API
            twiml_response = VoiceResponse()
            # שימוש בקוד שפה iw-IL ובקול Google Standard למינימום Latency
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
            print("TwiML response sent successfully to Twilio.")
            
        except Exception as e:
            print(f"Failed to send Twiml to active call {call_sid}: {e}")
            # אם הכל נכשל, ננתק את השיחה
            TWILIO_CLIENT.calls(call_sid).update(status='completed')


# --- נתיבי Webhook ו-WebSocket ---

@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """נקודת כניסה: Twilio Webhook שמתחיל את הזרמת המדיה."""
    
    # ודא שה-WEBSOCKET_URL מוגדר
    if WEBSOCKET_URL == "wss://example.up.railway.app/ws":
        print("CRITICAL ERROR: WEBSOCKET_URL not configured. Using fallback message.")
        resp = VoiceResponse()
        resp.say("מצטערים, המערכת אינה מוגדרת כרגע. בבקשה הגדר את משתנה הסביבה W S S Web Socket URL.", language=HEBREW_LANGUAGE_CODE_TWIML)
        return str(resp)

    resp = VoiceResponse()

    # הודעת פתיחה
    resp.say(
        "שלום וברוך הבא, אני העוזר הקולי שלך. המערכת עוברת כעת למצב זיהוי דיבור. בבקשה דבר לאחר הצפצוף.",
        language=HEBREW_LANGUAGE_CODE_TWIML,
        voice='Google.he-IL-Standard-A'
    )
    
    # חיבור ל-WebSocket
    connect = resp.connect()
    # שימוש ב-WEBSOCKET_URL המוגדר
    connect.stream(name='Hebrew_STT', url=WEBSOCKET_URL)
    
    return str(resp)

@sock.route('/ws')
def ws_stream(ws):
    """נקודת הקצה של ה-WebSocket שמקבלת את זרם האודיו מ-Twilio."""
    
    print("WebSocket connection established.")
    
    # משתנים לאיסוף הנתונים
    call_sid = None
    media_data_chunks = []
    
    try:
        while True:
            # קבלת הודעה מ-Twilio
            message = ws.receive()
            if message is None:
                break
            
            data = json.loads(message)
            event = data.get('event')

            if event == 'start':
                call_sid = data['start']['callSid']
                print(f"Media Stream Started for Call SID: {call_sid}")
            
            elif event == 'media':
                # נתונים בפורמט Base64
                base64_payload = data['media']['payload']
                # פענוח Base64 לנתוני אודיו בינאריים (PCM)
                audio_chunk = base64.b64decode(base64_payload)
                media_data_chunks.append(audio_chunk)
            
            elif event == 'stop':
                print(f"Media Stream Stopped for Call SID: {call_sid}. Total chunks: {len(media_data_chunks)}")
                # --- קריאה לפונקציה המרכזית לאחר סיום הדיבור ---
                if call_sid and media_data_chunks:
                    process_audio(call_sid, media_data_chunks)
                
                # ניקוי הנתונים
                media_data_chunks = []
                break

    except Exception as e:
        print(f"WebSocket Error: {e}")
        
    finally:
        print(f"WebSocket connection closed for Call SID: {call_sid}")

if __name__ == "__main__":
    # שינוי הפורט ל-5000 כברירת מחדל או לפי המשתנה ב-Railway
    port = int(os.environ.get("PORT", 5000))
    # הפעלת האפליקציה עם gunicorn כפי שנדרש ב-Railway
    # שים לב: ב-Railway, הפקודה Gunicorn מופעלת אוטומטית, אך אם מריץ מקומית:
    # app.run(host="0.0.0.0", port=port, debug=True)
    print(f"Starting Flask app on port {port}...")
    # אין צורך להפעיל את app.run כאן אם משתמשים ב-Procfile/Gunicorn ב-Railway
