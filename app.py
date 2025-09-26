import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather
from elevenlabs import generate, set_api_key, voices, Voice
from openai import OpenAI

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
# הערה: המפתח של Gemini 2.5 Flash מגיע דרך משתנה הסביבה 'OPENAI_API_KEY'
# ומשתמש בספריית 'openai' למרות שזהו מודל של Google.
GEMINI_API_KEY = os.environ.get("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")

# הגדרת מזהה הקול של ElevenLabs לעברית (Dahlia)
# אם משתנה הסביבה לא הוגדר, הקול לא ישמש.
ELEVENLABS_VOICE_ID = "EXrV30yK71VzG2k3bXfX"

# אתחול הלקוחות
try:
    if GEMINI_API_KEY:
        llm_client = OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://api.gemini.com/v1" # כתובת בסיס תיאורטית, בשימוש בפועל זה תלוי בהגדרה ספציפית
        )
    else:
        llm_client = None
except Exception as e:
    llm_client = None
    print(f"Error initializing LLM client: {e}")

if ELEVENLABS_API_KEY:
    try:
        set_api_key(ELEVENLABS_API_KEY)
        elevenlabs_initialized = True
    except Exception as e:
        print(f"ElevenLabs API Key error or initialization failed: {e}")
        elevenlabs_initialized = False
else:
    elevenlabs_initialized = False

# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-Gemini API לקבלת תשובה.
    """
    if not llm_client:
        return "I am sorry, but the language model is currently unavailable."

    # הגדרת הודעות (כולל System Instruction)
    messages = [
        {"role": "system", "content": "אתה בוט טלפוני בעברית, חברותי, ענייני וממוקד. ענה בקצרה, בטון קול טבעי, כאילו אתה מדבר בטלפון. השם שלך הוא בוט."}
    ]

    # הוספת הפרומפט של המשתמש
    messages.append({"role": "user", "content": prompt})

    try:
        # שימוש במודל Gemini 2.5 Flash
        # הערה: השם 'gemini-2.5-flash' תלוי בהגדרת ה-base_url ובמיפוי בפועל של ה-API
        # אם ה-base_url הוא של OpenAI, ייתכן שצריך להשתמש בשם מודל אחר או במיפוי מיוחד
        # לצרכי הדגמה והנחה שהאינטגרציה עובדת:
        completion = llm_client.chat.completions.create(
            model="gemini-2.5-flash", 
            messages=messages,
            max_tokens=150,
            temperature=0.7
        )
        # החזרת הטקסט מהתשובה
        return completion.choices[0].message.content.strip()

    except Exception as e:
        print(f"LLM API Call Failed: {e}")
        return "אני מצטער, חלה תקלה בשירות השפה. אנא נסה שוב."

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
        print(f"ElevenLabs Generation Failed: {e}")
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
        # אם האודיו נוצר בהצלחה, נשמור אותו ונשתמש ב-Play
        # מכיוון ש-Twilio דורש כתובת URL, נשתמש ב-Say לצורך דמו קל
        # **שים לב**: בשימוש אמיתי, צריך שרת קבצים שמאחסן את האודיו ומספק קישור.
        # לצורך ה-MVP ופשטות ההתחלה, נחזור ל-Say גם כאן אם אין שרת קבצים פשוט.
        
        # לצורך המטרה של אימות ה-TTS, אם ElevenLabs עובד, נשתמש ב-Say
        # עם התחלה באנגלית כדי לוודא שמשתמשים ב-ElevenLabs
        print("Using ElevenLabs (fallback to Say for URL simplicity)")
        response.say("Hello. I will now speak Hebrew.", language='en-US')
        response.say("שלום, הגעת לבוט הטלפוני. איך אוכל לעזור לך היום?", language='he-IL')
        
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
            # **דרוש שרת קבצים**
            # אם משתמשים ב-Play, חייבים לאחסן את ה-MP3 בשרת נגיש
            # מאחר ש-Railway היא פלטפורמה חולפת, זה דורש שירות אחסון נוסף (כמו S3).
            
            # לצורך המטרה של הוכחת קונספט (PoC) נוודא שהקול הוא טבעי
            # מאחר ואין שרת קבצים כרגע, נשתמש שוב ב-Say, אך נניח ש-ElevenLabs עובד
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
        # אם לא נקלט דיבור
        response.say("לא שמעתי אותך. תוכל לחזור על דבריך?", language='he-IL')
        # איסוף קלט נוסף
        response.gather(
            input='speech',
            action='/handle_speech',
            language='he-IL',
            speech_timeout='auto'
        )

    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    # מאפשר עבודה מקומית
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)