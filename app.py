import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather, Say
# חשוב: תיקון הייבוא של ElevenLabs בעקבות השגיאה בלוגים
from elevenlabs import generate, set_api_key, voices, Voice
from openai import OpenAI
# הוספת ספרייה לתמיכה בקבצי WAV, במידה ונרצה להשתמש בהמשך ב-Play
# import wave
# import io

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
# הערה: המפתח של Gemini 2.5 Flash מגיע דרך משתנה הסביבה 'OPENAI_API_KEY'
GEMINI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")

# הגדרת מזהה הקול של ElevenLabs לעברית (Dahlia)
ELEVENLABS_VOICE_ID = "EXrV30yK71VzG2k3bXfX"

# אתחול הלקוחות
llm_client = None
elevenlabs_initialized = False

try:
    if GEMINI_API_KEY:
        # אתחול לקוח OpenAI תוך שימוש ב-base_url עבור Gemini API
        # יש להחליף את ה-base_url בכתובת המדויקת אם היא שונה מהדיפולט
        llm_client = OpenAI(
            api_key=GEMINI_API_KEY,
            # בשימוש בפלטפורמות מסוימות, ניתן להשמיט base_url או להגדיר אותו בהתאם
            # base_url="https://api.gemini.com/v1" 
        )
        print("LLM Client initialized successfully.")
    else:
        print("Warning: OPENAI_API_KEY not found. LLM client not initialized.")
except Exception as e:
    print(f"Error initializing LLM client: {e}")

if ELEVENLABS_API_KEY:
    try:
        # אתחול ElevenLabs API
        set_api_key(ELEVENLABS_API_KEY)
        elevenlabs_initialized = True
        print("ElevenLabs initialized successfully.")
    except Exception as e:
        print(f"ElevenLabs API Key error or initialization failed: {e}")
        elevenlabs_initialized = False
else:
    print("Warning: ELEVENLABS_API_KEY not found. ElevenLabs not initialized.")


# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-Gemini API לקבלת תשובה.
    """
    if not llm_client:
        return "אני מצטער, אך מודל השפה אינו זמין כרגע. אנא פנה לתמיכה."

    # הגדרת הודעות (כולל System Instruction)
    messages = [
        {"role": "system", "content": "אתה בוט טלפוני בעברית, חברותי, ענייני וממוקד. ענה בקצרה ובטון קול טבעי, כאילו אתה מדבר בטלפון. השם שלך הוא בוט."}
    ]

    # הוספת הפרומפט של המשתמש
    messages.append({"role": "user", "content": prompt})

    try:
        # שימוש במודל Gemini 2.5 Flash
        completion = llm_client.chat.completions.create(
            model="gemini-2.5-flash", 
            messages=messages,
            max_tokens=150,
            temperature=0.7
        )
        # החזרת הטקסט מהתשובה
        return completion.choices[0].message.content.strip()

    except Exception as e:
        print(f"CRITICAL: LLM API Call Failed: {e}")
        return "אני מצטער, חלה תקלה חמורה בשירות השפה. אנא נסה שוב מאוחר יותר."


# --- פונקציית TTS (ElevenLabs) ---
def generate_audio_with_elevenlabs(text):
    """
    מנסה לייצר אודיו בעברית באמצעות ElevenLabs.
    מחזירה את האודיו בפורמט MP3 כבייטים, או None אם נכשל.
    """
    if not elevenlabs_initialized:
        return None

    try:
        # יצירת האודיו באמצעות ElevenLabs
        audio = generate(
            text=text,
            voice=ELEVENLABS_VOICE_ID,
            model="eleven_multilingual_v2" # מודל רב לשוני תומך בעברית
        )
        # ElevenLabs מחזירה איטרטור (אודיו כבייטים), נחבר אותו לבייטס אחד
        return b"".join(audio)

    except Exception as e:
        print(f"ERROR: ElevenLabs Generation Failed: {e}")
        # במקרה של כשל, נחזיר None כדי שהבוט יחזור ל-Twilio Say
        return None

# --- Webhook לטיפול בשיחה נכנסת (התחלה) ---
@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    נקודת הכניסה לשיחת הטלפון. מחזירה TwiML עם בקשה לקלט קולי.
    """
    response = VoiceResponse()
    
    # טקסט ראשוני
    initial_prompt = "שלום, הגעת לבוט הטלפוני. איך אוכל לעזור לך היום?"
    
    # מנסה להשתמש ב-ElevenLabs
    audio_data = generate_audio_with_elevenlabs(initial_prompt)
    
    if audio_data:
        # אם ElevenLabs עובד, נשתמש ב-Say עם התחלה באנגלית כדי לוודא
        # ששירות ה-TTS הפעיל הוא ElevenLabs (בגלל שאין לנו אחסון קבצים ב-Railway)
        print("SUCCESS: ElevenLabs initialized. Using fallback Say for PoC.")
        response.say("Hello. I will now speak Hebrew.", language='en-US')
        response.say(initial_prompt, language='he-IL')
        
    else:
        # אם ElevenLabs נכשל או לא הוגדר, נשתמש ב-Say הסטנדרטי של Twilio
        print("FALLBACK: Using Twilio default Say (ElevenLabs failed or not initialized).")
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
    
    # קבלת הטקסט המדובר מ-Twilio
    spoken_text = request.form.get('SpeechResult')
    
    if spoken_text:
        print(f"User said: {spoken_text}")
        
        # קריאה ל-LLM
        llm_response_text = call_llm_api(spoken_text)
        print(f"LLM response: {llm_response_text}")

        # מנסה להשתמש ב-ElevenLabs
        audio_data = generate_audio_with_elevenlabs(llm_response_text)
        
        if audio_data:
            # ElevenLabs עובד - נשתמש בו (גיבוי ל-Say בשל מגבלות אחסון)
            print("SUCCESS: Responding with ElevenLabs (fallback to Say).")
            response.say(llm_response_text, language='he-IL')
            
        else:
            # ElevenLabs נכשל או לא הוגדר - נשתמש ב-Say הסטנדרטי של Twilio
            print("FALLBACK: Responding with Twilio default Say.")
            response.say(llm_response_text, language='he-IL')

        # איסוף קלט נוסף כדי להמשיך את השיחה (לולאה)
        response.gather(
            input='speech',
            action='/handle_speech',
            language='he-IL',
            speech_timeout='auto'
        )
        
    else:
        # אם לא נקלט דיבור
        response.say("לא שמעתי אותך. תוכל לחזור על דבריך?", language='he-IL')
        # איסוף קלט נוסף
        response.gather(
            input='speech',
            action='/handle_speech',
            language='he-IL',
            speech_timeout='auto'
        )

    # אם השיחה מסתיימת (לדוגמה, המשתמש לא אמר כלום), ננתק
    # response.hangup() 
    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    # מאפשר עבודה מקומית
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
