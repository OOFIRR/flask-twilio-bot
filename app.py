from flask import Flask, request, Response, make_response
from dotenv import load_dotenv
from google.cloud import texttospeech
from google.oauth2 import service_account
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai
import os
import json
import uuid
from urllib.parse import urljoin
import traceback
import threading
import time

print("🚀 Flask app is loading...")

# --- אתחול גלובלי (Global Initialization) ---

# טעינת משתני סביבה
load_dotenv(dotenv_path='env/.env')

app = Flask(__name__)

# זיכרון שיחה זמני (In-memory session context). 
# הערה: יש להחליף ב-Redis/Firestore לסביבת פרודקשן אמיתית.
session_memory = {}

# ודא שספריית ה-static קיימת (חשוב במיוחד לסביבות ענן כמו Railway!)
STATIC_DIR = os.path.join(os.getcwd(), 'static')
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)
    print(f"📁 Created static directory at: {STATIC_DIR}")

# משתני API (נטענים ברמת המודול)
print("🔑 Checking env variables...")
openai.api_key = os.getenv("OPENAI_API_KEY")
if openai.api_key:
    print("OPENAI_API_KEY loaded: ✅")
else:
    print("❌ Missing OPENAI_API_KEY")

google_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if google_creds_json:
    print(f"GOOGLE_APPLICATION_CREDENTIALS_JSON loaded: ✅")
else:
    print("❌ Missing GOOGLE_APPLICATION_CREDENTIALS_JSON")


# --- פונקציות עזר (Helper Functions) ---

def get_tts_client():
    """מאתחל ומחזיר את לקוח Google Text-to-Speech."""
    # הגנה: מוודא שהמשתנה קיים לפני ניסיון הפיענוח
    if not google_creds_json:
        raise EnvironmentError("Missing Google TTS credentials (JSON string).")
        
    try:
        credentials_dict = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        # הלקוח נוצר כאן (בתוך ה-request)
        return texttospeech.TextToSpeechClient(credentials=credentials)
    except Exception as e:
        print(f"❌ Google TTS init failed: {e}")
        # אם יש שגיאה, נזרק אותה כדי שה-try/except בראוט יתפוס
        raise


def delete_file_later(path, delay=30):
    """מוחק קובץ באופן אסינכרוני לאחר השהייה."""
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
            print(f"🗑️ Deleted file: {path}")
        except Exception as e:
            # מתעלם משגיאות מחיקה שקטות
            print(f"❌ Failed to delete file {path}:", e)
    
    # מפעיל את המחיקה ב-thread נפרד כדי לא לחסום את התגובה ל-Twilio
    threading.Thread(target=_delete).start()


# --- ראוטים של Flask ---

@app.route("/", methods=["GET"])
def index():
    """ראוט בדיקת חיים (Health Check)."""
    print("✅ GET / called")
    # תשובה תקינה (200) מוודאת שהשרת רץ ונגיש
    return "✅ Flask server is running on Railway!"


@app.route("/twilio/answer", methods=["POST"])
def twilio_answer():
    """נקודת הכניסה לשיחה חדשה מ-Twilio."""
    try:
        print("📞 New call: /twilio/answer")
        
        response = VoiceResponse()

        # Gather: מתחיל האזנה לקול המשתמש
        gather = Gather(
            input='speech',
            action='/twilio/process',  # הולך לראוט שמטפל בתשובה
            method='POST',
            language='he-IL',
            speech_timeout='auto'
        )
        gather.say("שלום! איך אפשר לעזור לך היום?", language='he-IL')
        response.append(gather)
        
        # Fallback אם המשתמש לא אומר כלום
        response.say("לא קיבלתי תשובה. להתראות!", language='he-IL')

        xml_str = str(response)
        # מוודא שה-headers מוגדרים נכון עבור Twilio
        return Response(xml_str, status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

    except Exception as e:
        print("❌ ERROR in /twilio/answer:", e)
        traceback.print_exc()
        return Response("Internal Server Error", status=500)


@app.route("/twilio/process", methods=["POST"])
def twilio_process():
    """מטפל בקלט הקולי, שולח ל-GPT, יוצר אודיו וממשיך את השיחה."""
    try:
        print("🛠️ Request to /twilio/process")
        
        # שולף את הקלט ואת מזהה השיחה (CallSid)
        user_input = request.form.get('SpeechResult')
        call_sid = request.form.get('CallSid')

        # --- טיפול בקלט חסר ---
        if not user_input:
            print("⚠️ No speech input")
            response = VoiceResponse()
            response.say("לא שמעתי אותך. תוכל לנסות שוב?", language='he-IL')
            # מתחיל Gather מחדש
            gather = Gather(
                input='speech',
                action='/twilio/process',
                method='POST',
                language='he-IL',
                speech_timeout='auto'
            )
            gather.say("מה תרצה לדעת?", language='he-IL')
            response.append(gather)
            return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

        print(f"📞 CallSid: {call_sid}")
        print("🗣️ User said:", user_input)

        # --- ניהול זיכרון שיחה (Session Management) ---
        # טוען היסטוריה או מתחיל חדשה
        messages = session_memory.get(call_sid, [])
        # הוסף את הודעת המשתמש הנוכחית
        messages.append({"role": "user", "content": user_input})
        
        # --- קריאה ל-OpenAI ---
        gpt_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,  # שולח את כל ההיסטוריה
            max_tokens=150,
            temperature=0.7
        )
        bot_text = gpt_response.choices[0].message.content.strip()
        print("🤖 GPT says:", bot_text)

        # עדכון זיכרון השיחה
        messages.append({"role": "assistant", "content": bot_text})
        session_memory[call_sid] = messages

        # --- יצירת אודיו (Google TTS) ---
        tts_client = get_tts_client()
        synthesis_input = texttospeech.SynthesisInput(text=bot_text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="he-IL",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
        audio_config = texttospeech.AudioConfig(
            # הגדרות קריטיות: 8kHz, LINEAR16, נדרש ל-Twilio Play
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=8000 
        )
        response_tts = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        # --- שמירת קובץ ומחיקה ---
        unique_id = str(uuid.uuid4())
        output_path = os.path.join(STATIC_DIR, f"output_{unique_id}.wav") # משתמש ב-STATIC_DIR
        with open(output_path, "wb") as out:
            out.write(response_tts.audio_content)
            
        # מפעיל מחיקה לאחר 30 שניות
        delete_file_later(output_path, delay=30) 

        # יצירת URL ציבורי
        wav_url = urljoin(request.host_url, f"static/output_{unique_id}.wav")
        print(f"🔊 Playing audio: {wav_url}")

        # --- יצירת תגובת TwiML ---
        response = VoiceResponse()
        response.play(wav_url) # מנגן את התשובה

        # ממשיך את לולאת השיחה
        gather = Gather(
            input='speech',
            action='/twilio/process',
            method='POST',
            language='he-IL',
            speech_timeout='auto'
        )
        gather.say("יש לך שאלה נוספת?", language='he-IL')
        response.append(gather)

        return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

    except Exception as e:
        print("❌ ERROR in /twilio/process:", e)
        traceback.print_exc()
        # תשובת שגיאה ידידותית למשתמש
        response = VoiceResponse()
        response.say("אירעה שגיאה. נסה שוב מאוחר יותר.", language='he-IL')
        return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Note: Gunicorn הוא זה שמפעיל בפועל את האפליקציה ב-Railway.
    app.run(debug=True, host="0.0.0.0", port=port)