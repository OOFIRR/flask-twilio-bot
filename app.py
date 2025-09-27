# -*- coding: utf-8 -*-
import os
import time
import requests
import json
import uuid
import tempfile
from flask import Flask, request, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
# ייבוא נכון של elevenlabs (עם set_api_key, generate ו-save)
from elevenlabs import set_api_key, generate, save
from elevenlabs.api.error import APIError

# --- 1. הגדרות וטעינת מפתחות API ---

# טוען משתני סביבה. אם מפתח ElevenLabs או OpenAI חסר, השרת ימשיך לרוץ
# וישתמש בגיבויים או יפספס את היכולת הרלוונטית, אך לא יקרוס.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# הגדרת ElevenLabs API אם המפתח קיים
if ELEVENLABS_API_KEY:
    try:
        set_api_key(ELEVENLABS_API_KEY)
        print("ElevenLabs API key successfully loaded.")
    except Exception as e:
        print(f"Error setting ElevenLabs API key: {e}")

# שינוי קריטי: מזהה הקול האישי שלך מ-ElevenLabs
HEBREW_VOICE_ID = "MTfTLiL7VOpWnuOQqiyV" # *** ID הקול האישי של המשתמש ***

# היסטוריית שיחה (לצורך שמירת הקונטקסט)
CALL_CONTEXT = {}

# --- 2. הגדרות שרת Flask ---
app = Flask(__name__, static_url_path='/static', static_folder='static')

# --- 3. פונקציות עזר ---

def call_llm_api(prompt, call_sid):
    """מתקשר ל-LLM של Gemini באמצעות מפתח OpenAI API."""
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set. Cannot call LLM.")
        return "אני מצטער, מודל השפה אינו זמין כרגע."

    # טוען היסטוריית שיחה או מתחיל חדשה
    history = CALL_CONTEXT.get(call_sid, [])
    
    # הוספת הודעת משתמש חדשה
    history.append({
        "role": "user",
        "parts": [{"text": prompt}]
    })

    # מגדיר את ההנחיה המערכתית (System Instruction)
    system_instruction = {
        "parts": [{"text": "אתה עוזר קולי בעברית שמספק תשובות קצרות ותכליתיות. ענה בצורה ידידותית, קצרה וטבעית. אם המשתמש שואל שאלות מורכבות מדי, בקש ממנו לשאול שאלות פשוטות יותר."}]
    }

    # כתובת ה-API של Gemini (תיקון אחרון לשגיאת 404)
    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={OPENAI_API_KEY}"
    
    payload = {
        "contents": history,
        "systemInstruction": system_instruction,
    }

    try:
        # הגדרת אקספוננציאלית לחזרה (Exponential Backoff)
        max_retries = 3
        for attempt in range(max_retries):
            response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload)
            
            if response.status_code == 429 and attempt < max_retries - 1:
                # 429 Too Many Requests - retry
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue

            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            break # Success, exit loop
        else:
             # This line executes if the loop completes without a successful response
            response.raise_for_status()


        result = response.json()
        candidate = result.get('candidates', [{}])[0]
        text_response = candidate.get('content', {}).get('parts', [{}])[0].get('text', "מודל השפה לא הצליח להגיב.")

        # הוספת תגובת המודל להיסטוריה
        history.append({
            "role": "model",
            "parts": [{"text": text_response}]
        })
        CALL_CONTEXT[call_sid] = history
        return text_response

    except requests.exceptions.RequestException as e:
        print(f"Error calling LLM API (Status: {response.status_code if 'response' in locals() else 'Unknown'}): {e}")
        return "אני מצטער, חלה שגיאה בחיבור למודל השפה."


def generate_tts_wav(text_to_speak, call_sid):
    """
    יוצר קובץ WAV באמצעות ElevenLabs ומחזיר את הנתיב לקובץ הסטטי.
    אם ElevenLabs נכשל, מחזיר None.
    """
    if not ELEVENLABS_API_KEY:
        print("ElevenLabs API key missing. Falling back to Twilio Say.")
        return None
        
    # בדיקה נוספת למקרה שהמשתמש שכח להחליף את ה-ID
    if HEBREW_VOICE_ID == "YOUR_PERSONAL_VOICE_ID_HERE":
         print("Warning: Voice ID is the default placeholder. Falling back to Twilio Say.")
         return None

    try:
        # יצירת קובץ WAV ייחודי ושמירה בתיקיית static
        filename = f"{call_sid}_{int(time.time())}.wav"
        filepath = os.path.join(app.static_folder, filename)
        
        # יצירת האודיו באמצעות ElevenLabs
        audio = generate(
            text=text_to_speak,
            voice=HEBREW_VOICE_ID, # משתמש בקול המשובט שלך
            model="eleven_multilingual_v2" 
        )
        
        # שמירת קובץ ה-PCM שהתקבל כקובץ WAV
        save(audio, filepath)
        
        # כתובת URL ציבורית לקובץ (בתוך שרת Flask)
        static_url = f"/static/{filename}"
        return static_url

    except APIError as e:
        print(f"ElevenLabs API Error: {e}. Falling back to Twilio Say.")
        return None
    except Exception as e:
        print(f"General TTS Error: {e}. Falling back to Twilio Say.")
        return None
    
    
def cleanup_wav_file(filepath):
    """מוחק קובץ WAV מתיקיית static."""
    # ניקוי בטוח יותר
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            print(f"Cleaned up file: {filepath}")
    except OSError as e:
        print(f"Error cleaning up file {filepath}: {e}")

# --- 4. נתיבים של Webhook ---

@app.route("/twilio/answer", methods=['POST'])
def answer_call():
    """נקודת הכניסה לשיחה. מחזירה TwiML עם הודעת פתיחה ו-Gather."""
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')
    
    # בדיקה אם אפשר להשתמש ב-ElevenLabs
    welcome_text = "שלום! איך אפשר לעזור לך היום?"
    wav_url = generate_tts_wav(welcome_text, call_sid)

    # אם ElevenLabs הצליח, נשתמש ב-Play. אחרת, נשתמש ב-Say של Twilio (קול אנגלי)
    if wav_url:
        resp.play(url=request.url_root + wav_url)
    else:
        # גיבוי: משתמשים ב-Twilio Say
        # Twilio Say אינו תומך בקולות משובטים, לכן נחזור לקול ברירת המחדל שלהם
        resp.say(welcome_text, voice='Polly.Amy', language='he-IL')

    # הגדרת Gather לקבלת קול המשתמש
    gather = Gather(
        input='speech',
        action='/twilio/handle_speech',
        method='POST',
        timeout=3, # זמן המתנה לדיבור
        speechTimeout='auto', # Twilio תזהה שתיקה
        language='he-IL'
    )
    
    resp.append(gather)
    
    return str(resp)

@app.route("/twilio/handle_speech", methods=['POST'])
def handle_speech():
    """מטפל בקלט הקולי (SpeechToText) מהמשתמש."""
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')
    user_speech = request.values.get('SpeechResult')
    
    # מוחק את הקונטקסט של השיחה אם לא נקלט דיבור
    if user_speech is None or user_speech.strip() == "":
        if call_sid in CALL_CONTEXT:
            del CALL_CONTEXT[call_sid]
        
        # הודעה חוזרת אם לא נקלט דיבור
        repeat_text = "לא שמעתי אותך בבירור. נסה שוב."
        wav_url = generate_tts_wav(repeat_text, call_sid)
        
        if wav_url:
            resp.play(url=request.url_root + wav_url)
        else:
            resp.say(repeat_text, voice='Polly.Amy', language='he-IL')

        # חוזר למצב הקשבה
        gather = Gather(
            input='speech',
            action='/twilio/handle_speech',
            method='POST',
            timeout=3,
            speechTimeout='auto',
            language='he-IL'
        )
        resp.append(gather)
        return str(resp)


    print(f"User said: {user_speech}")

    # קריאה למודל השפה
    llm_response = call_llm_api(user_speech, call_sid)
    
    # יצירת קובץ WAV לתשובה
    wav_url = generate_tts_wav(llm_response, call_sid)
    
    if wav_url:
        # השמעת הקובץ החדש
        resp.play(url=request.url_root + wav_url)
        # ניקוי קובץ ה-WAV לאחר ההשמעה
        cleanup_wav_file(os.path.join(app.static_folder, os.path.basename(wav_url)))
    else:
        # גיבוי: שימוש ב-Twilio Say
        resp.say(llm_response, voice='Polly.Amy', language='he-IL')

    # חזרה להקשבה
    gather = Gather(
        input='speech',
        action='/twilio/handle_speech',
        method='POST',
        timeout=3,
        speechTimeout='auto',
        language='he-IL'
    )
    
    resp.append(gather)
    
    return str(resp)

# --- 5. מסלול בריאות לבדיקה ---

@app.route("/")
def health_check():
    """מסלול לבדיקת תקינות השרת."""
    return "Flask server is running on Railway and ready for Twilio calls!"

# --- 6. הפעלת השרת ---

if __name__ == "__main__":
    # יצירת תיקיית static אם אינה קיימת
    if not os.path.exists(app.static_folder):
        os.makedirs(app.static_folder)
    
    # הפעלת השרת על הפורט של Railway
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
