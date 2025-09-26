import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

# --- תיקון ייבוא ElevenLabs לשימוש ב-Client Object ---
# אנו מייבאים את המחלקה ElevenLabs כדי להשתמש בשיטת Generate יציבה יותר
from elevenlabs.client import ElevenLabs 
from elevenlabs import set_api_key, Voice 

from openai import OpenAI

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
GEMINI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")

# הגדרת מזהה הקול של ElevenLabs לעברית (Dahlia)
ELEVENLABS_VOICE_ID = "EXrV30yK71VzG2k3bXfX"
ELEVENLABS_MODEL = "eleven_multilingual_v2" 

# אתחול הלקוחות
try:
    if GEMINI_API_KEY:
        # אתחול לקוח LLM (Gemini 2.5 Flash)
        llm_client = OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://api.gemini.com/v1" # כתובת בסיס תיאורטית, בשימוש בפועל זה תלוי בהגדרה ספציפית
        )
    else:
        llm_client = None
except Exception as e:
    llm_client = None
    print(f"Error initializing LLM client: {e}")

elevenlabs_client = None
elevenlabs_initialized = False

try:
    if ELEVENLABS_API_KEY:
        # אתחול לקוח ElevenLabs (השתמשנו ב-set_api_key כדי לוודא שכל המודולים מקבלים את המפתח)
        set_api_key(ELEVENLABS_API_KEY)
        elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        elevenlabs_initialized = True
        print("ElevenLabs client initialized successfully.")
    else:
        print("ElevenLabs API Key is missing.")
except Exception as e:
    print(f"ElevenLabs API Key error or initialization failed: {e}")
    elevenlabs_initialized = False


# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-Gemini API לקבלת תשובה.
    """
    if not llm_client:
        return "אני מצטער, אבל מודל השפה כרגע אינו זמין."

    messages = [
        {"role": "system", "content": "אתה בוט טלפוני בעברית, חברותי, ענייני וממוקד. ענה בקצרה, בטון קול טבעי, כאילו אתה מדבר בטלפון. השם שלך הוא בוט."}
    ]
    messages.append({"role": "user", "content": prompt})

    try:
        completion = llm_client.chat.completions.create(
            model="gemini-2.5-flash", 
            messages=messages,
            max_tokens=150,
            temperature=0.7
        )
        return completion.choices[0].message.content.strip()

    except Exception as e:
        print(f"LLM API Call Failed: {e}")
        return "אני מצטער, חלה תקלה בשירות השפה. אנא נסה שוב."

# --- פונקציית TTS (ElevenLabs) ---
def generate_audio_with_elevenlabs(text):
    """
    מנסה לייצר אודיו בעברית באמצעות ElevenLabs תוך שימוש באובייקט הלקוח.
    מחזירה את האודיו בפורמט MP3 כבייטים, או None אם נכשל.
    """
    if not elevenlabs_initialized or not elevenlabs_client:
        return None

    try:
        # שימוש בשיטת ה-Client במקום בפונקציית generate הגלובלית
        audio = elevenlabs_client.generate(
            text=text,
            voice=ELEVENLABS_VOICE_ID,
            model=ELEVENLABS_MODEL
        )
        
        # ElevenLabs מחזירה איטרטור, נחבר אותו לבייטס אחד
        return b"".join(audio)

    except Exception as e:
        print(f"ElevenLabs Generation Failed (using client): {e}")
        return None

# --- Webhook לטיפול בשיחה נכנסת (התחלה) ---
@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    נקודת הכניסה לשיחת הטלפון. מחזירה TwiML עם בקשה לקלט קולי.
    """
    response = VoiceResponse()
    
    initial_prompt = "שלום, הגעת לבוט הטלפוני. איך אוכל לעזור לך היום?"
    
    # מנסה להשתמש ב-ElevenLabs
    audio_data = generate_audio_with_elevenlabs(initial_prompt)
    
    if audio_data:
        # אם האודיו נוצר בהצלחה
        print("Using ElevenLabs (fallback to Say for URL simplicity)")
        # שימוש ב-Say כדי לוודא שה-TTS עובד, מכיוון שאין לנו שרת אחסון קבצים
        # הקול האנגלי רק כדי לסמן הצלחה ברורה של ה-TTS API
        response.say("Hello. I will now speak Hebrew.", language='en-US')
        response.say(initial_prompt, language='he-IL')
        
    else:
        # אם ElevenLabs נכשל או לא הוגדר, נשתמש ב-Say הסטנדרטי של Twilio
        print("Using Twilio default Say (ElevenLabs failed or not initialized)")
        response.say(initial_prompt, language='he-IL')

    # בקשה לאיסוף הקלט הקולי של המשתמש (Gather)
    response.gather(
        input='speech',
        action='/handle_speech',
        language='he-IL',
        speech_timeout='auto'
    )
    
    return str(response)

# --- Webhook לטיפול בקלט קולי ---
@app.route("/handle_speech", methods=['POST'])
def handle_speech():
    """
    מקבל את הדיבור של המשתמש, שולח ל-LLM ומחזיר את התשובה.
    """
    response = VoiceResponse()
    spoken_text = request.form.get('SpeechResult')
    
    if spoken_text:
        print(f"User said: {spoken_text}")
        
        # קריאה ל-LLM
        llm_response_text = call_llm_api(spoken_text)
        print(f"LLM response: {llm_response_text}")

        # מנסה להשתמש ב-ElevenLabs
        audio_data = generate_audio_with_elevenlabs(llm_response_text)
        
        if audio_data:
            print("Using ElevenLabs (fallback to Say for URL simplicity)")
            response.say(llm_response_text, language='he-IL')
            
        else:
            # אם ElevenLabs נכשל או לא הוגדר, נשתמש ב-Say הסטנדרטי של Twilio
            print("Using Twilio default Say (ElevenLabs failed or not initialized)")
            response.say(llm_response_text, language='he-IL')

        # איסוף קלט נוסף כדי להמשיך את השיחה (לולאה)
        response.gather(
            input='speech',
            action='/handle_speech',
            language='he-IL',
            speech_timeout='auto'
        )
        
    else:
        response.say("לא שמעתי אותך. תוכל לחזור על דבריך?", language='he-IL')
        response.gather(
            input='speech',
            action='/handle_speech',
            language='he-IL',
            speech_timeout='auto'
        )

    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
