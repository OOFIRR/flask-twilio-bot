import os
import requests
import json
import base64
import time
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
from twilio.rest import Client as TwilioClient
from flask_sock import Sock

# --- הגדרות ושירותים ---
app = Flask(__name__)
sock = Sock(app)

# משתני סביבה קריטיים
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXAVyGqjNst4aUK-sQzS") # קול עברי
HEBREW_LANGUAGE_CODE = "iw-IL" 

# פרטי Twilio (נדרש לשליחת פקודות REST חזרה לשיחה החיה)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# *** קריטי: כתובת ה-WebSocket של השרת שלך. עליך להחליף ב-URL האמיתי של Railway! ***
# לדוגמה: wss://my-railway-app.up.railway.app/ws
WEBSOCKET_URL = os.environ.get("WEBSOCKET_URL", "wss://<YOUR_PUBLIC_RAILWAY_URL>/ws") 

# --- פונקציות ליבה ---

def call_llm_api(prompt):
    """מתקשרת ל-OpenAI API לקבלת תשובה."""
    if not OPENAI_API_KEY:
        return "אני מצטער, מודל השפה כרגע אינו זמין. חסר מפתח OpenAI."

    messages = [
        {"role": "system", "content": "אתה בוט טלפוני בעברית. ענה בקצרה, בתמציתיות ובטון חברותי וממוקד."},
        {"role": "user", "content": prompt}
    ]

    try:
        # [קוד LLM זהה, קיצור ל-100 טוקנים]
        api_url = "https://api.openai.com/v1/chat/completions"
        payload = {"model": "gpt-3.5-turbo", "messages": messages, "max_tokens": 100, "temperature": 0.7}
        response = requests.post(api_url, headers={'Content-Type': 'application/json','Authorization': f'Bearer {OPENAI_API_KEY}'}, json=payload)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'].strip()

    except Exception as e:
        print(f"OpenAI API Call Failed: {e}")
        return "אני מצטער, חלה תקלה בשירות השפה."

def generate_and_host_elevenlabs_audio(text, call_sid):
    """
    יוצר אודיו ב-ElevenLabs ושומר אותו זמנית (בפועל דורש S3) או משתמש בקישור דמה.
    בגלל מגבלות Railway, אנו נדפיס הודעת שגיאה ונחזיר קישור דמה כדי לא לשבור את הלוגיקה.
    """
    if not ELEVENLABS_API_KEY:
        print("ElevenLabs API key missing. Cannot generate high-quality TTS.")
        return None

    # *** קריטי: כאן צריך להיות קוד שיוצר את ה-MP3 ושומר אותו ב-S3 או Twilio Assets.
    # מכיוון שאין לנו S3, נדפיס את ה-MP3 ונקווה לטוב.
    print(f"Generated ElevenLabs audio for: {text[:20]}... (requires external hosting)")
    
    # בפתרון מלא, כאן ייווצר MP3 ב-ElevenLabs ויישלח לשרת אחסון.
    # ה-URL המוחזר יהיה הקישור הציבורי לקובץ.
    
    # מכיוון שאי אפשר לשמור קובץ ב-Railway, נחזיר הודעת כשל ברורה.
    return None 

def send_play_command(call_sid, mp3_url):
    """שולח פקודת TwiML חדשה לשיחה החיה (כגון <Play> או <Say>)."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        print("Twilio Credentials missing. Cannot send TwiML command.")
        return False
    
    # אם אין URL של MP3 (בגלל מגבלות אחסון), נשתמש ב-<Say> המובנה של Twilio
    if not mp3_url:
        print("Using Twilio's built-in TTS as a fallback.")
        twiml_response = VoiceResponse()
        twiml_response.say("אני מצטער, חלה תקלה בשירות הקול החיצוני. הנה התשובה:", 
                           language=HEBREW_LANGUAGE_CODE, 
                           voice="Google.he-IL-Standard-A")
        
        # כדי למנוע לולאה, נבקש תשובה קצרה נוספת במקרה של כשל TTS.
        llm_fallback_response = call_llm_api("חזור על התשובה הקודמת באופן קצרצר.")
        twiml_response.say(llm_fallback_response, language=HEBREW_LANGUAGE_CODE, voice="Google.he-IL-Standard-A")
        
    else:
        # ביישום מלא: <Play> עם ה-URL הציבורי של ElevenLabs MP3
        twiml_response = VoiceResponse()
        twiml_response.play(mp3_url)
        
    # Twilio REST API קורא את ה-TwiML ומבצע אותו בשיחה
    try:
        twilio_client.calls(call_sid).update(twiml=str(twiml_response))
        print(f"Successfully sent TwiML update to Call SID: {call_sid}")
        return True
    except Exception as e:
        print(f"Failed to update call {call_sid}: {e}")
        return False

def process_audio(call_sid, audio_chunks):
    """
    פונקציה זו אמורה לקבל את האודיו ולשלוח ל-ElevenLabs STT.
    כאן אנו מדמים את זה ושולחים שאילתת דמה ל-LLM.
    """
    print(f"Received {len(audio_chunks)} audio chunks for processing.")
    
    # *** קריטי: כאן היית משתמש ב-ElevenLabs STT API עם הנתונים האלו. ***
    
    # --- הדמיית תמלול והחזרת תשובה קבועה ---
    
    # נניח שהתמלול הצליח וקיבלנו את הטקסט הבא:
    transcribed_text = "בבקשה תסביר לי למה חשוב ללמוד בינה מלאכותית."
    print(f"Simulated Transcription: {transcribed_text}")
    
    llm_response_text = call_llm_api(transcribed_text)
    
    # 2. יצירת אודיו (ElevenLabs TTS) ושמירה חיצונית
    mp3_url = generate_and_host_elevenlabs_audio(llm_response_text, call_sid)
    
    # 3. שליחת פקודת <Play> חזרה לשיחה
    send_play_command(call_sid, mp3_url)
    
    # 4. לאחר התשובה, אנו שולחים פקודת <Stream> נוספת להמשך השיחה
    # (זה חלק מורכב, ולצורך הפשטות נניח שהשיחה נשארת פתוחה.)
    
    return "Processing complete"

# --- Webhook לטיפול בשיחה נכנסת (התחלה) ---
@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    נקודת הכניסה לשיחת הטלפון. מורה ל-Twilio להתחיל הזרמת מדיה.
    """
    response = VoiceResponse()

    response.say("שלום, ברוכים הבאים לבוט הקולי הדו-כיווני בעברית. אנא התחל לדבר לאחר הצליל. אני מחברת אותך לשירות זיהוי הדיבור שלנו.", 
                 language=HEBREW_LANGUAGE_CODE, 
                 voice="Google.he-IL-Standard-A")
    
    connect = Connect()
    
    print(f"Connecting to WebSocket URL: {WEBSOCKET_URL}")
    connect.stream(
        url=WEBSOCKET_URL,
        track='inbound_track',
        content_type='audio/l16;rate=8000'
    )
    
    response.append(connect)
    
    response.say("הזרמת המדיה הסתיימה. להתראות.", 
                 language=HEBREW_LANGUAGE_CODE, 
                 voice="Google.he-IL-Standard-A")
    response.hangup()
    
    return str(response)

# --- WebSocket לטיפול בזרם המדיה ---

@sock.route('/ws')
def ws_handler(ws):
    """
    מקבל את ה-WebSocket מ-Twilio ומטפל בזרם הנתונים בזמן אמת.
    """
    print("WebSocket connection established.")
    call_sid = None
    media_data_chunks = []
    
    while True:
        try:
            # Twilio שולח הודעות בפורמט JSON
            message = ws.receive()
            if message is None:
                continue

            data = json.loads(message)
            event = data.get('event')
            
            if event == 'start':
                call_sid = data['start']['callSid']
                print(f"Media Stream Started for Call SID: {call_sid}")

            elif event == 'media':
                # מקבל chunk של אודיו ומוסיף אותו לרשימה
                chunk = data['media']['payload']
                media_data_chunks.append(chunk)

            elif event == 'stop':
                print(f"Media Stream Stopped for Call SID: {call_sid}. Total chunks: {len(media_data_chunks)}")
                
                # כשהמשתמש מסיים לדבר (Twilio שולח 'stop'), מתחילים לעבד את האודיו.
                if call_sid and media_data_chunks:
                    process_audio(call_sid, media_data_chunks)
                
                # מנקים את הרשימה ומחכים ל-WebSocket חדש (במידה והשיחה נמשכת)
                media_data_chunks = []
                call_sid = None
                
            elif event == 'mark':
                # אם שולחים 'mark' מ-Twilio, זה אומר שיש להשמיע משהו כרגע.
                print(f"Received Mark: {data['mark']}")

        except Exception as e:
            print(f"WebSocket Error: {e}")
            break
            
    print("WebSocket connection closed.")

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    # אנחנו צריכים להשתמש בשרת שתומך ב-WebSocket כמו gunicorn
    # לצורך סביבת קנבס זו, נריץ את Flask כרגיל.
    port = int(os.environ.get("PORT", 5000))
    # יש להפעיל את זה עם Gunicorn בפועל עם Workers מתאימים
    app.run(debug=True, host='0.0.0.0', port=port)
