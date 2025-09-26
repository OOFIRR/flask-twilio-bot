import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

# הסרנו ייבוא ElevenLabs

from openai import OpenAI

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
GEMINI_API_KEY = os.environ.get("OPENAI_API_KEY")
HEBREW_LANGUAGE_CODE = "he-IL" # קוד שפה תקני לעברית

# אתחול הלקוח של Gemini
try:
    if GEMINI_API_KEY:
        # אתחול לקוח LLM (Gemini 2.5 Flash - כתובת בסיס תיאורטית)
        llm_client = OpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://api.gemini.com/v1"
        )
    else:
        llm_client = None
except Exception as e:
    llm_client = None
    print(f"Error initializing LLM client: {e}")


# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-Gemini API לקבלת תשובה.
    """
    if not llm_client:
        return "אני מצטער, אבל מודל השפה כרגע אינו זמין."

    messages = [
        {"role": "system", "content": "אתה בוט טלפוני בעברית, חברותי, ענייני וממוקד. ענה בקצרה, בטון קול טבעי, כאילו אתה מדבר בטלפון."}
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


# --- Webhook לטיפול בשיחה נכנסת (התחלה) ---
@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    נקודת הכניסה לשיחת הטלפון. מחזירה TwiML עם בקשה לקלט קולי.
    """
    response = VoiceResponse()
    
    # שינוי ההודעה לשפה אנגלית לבדיקה
    initial_prompt = "Hello. Please speak after the beep. How can I help you today?"
    
    # שימוש ב-Say המובנה של Twilio
    print("Using Twilio default Say with English TTS to confirm sound is working.")
    # השתמשו ב-en-US עבור TTS כדי לוודא שקול כלשהו נשמע
    response.say(initial_prompt, language='en-US') 

    # בקשה לאיסוף הקלט הקולי של המשתמש (Gather)
    # נשארים על עברית עבור זיהוי דיבור
    response.gather(
        input='speech',
        action='/handle_speech',
        language=HEBREW_LANGUAGE_CODE,
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

        # שימוש ב-Say המובנה של Twilio - מעבר לאנגלית לצורך הבדיקה
        print("Using Twilio default Say with English TTS to confirm response.")
        response.say(llm_response_text, language='en-US')

        # איסוף קלט נוסף כדי להמשיך את השיחה (לולאה)
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            speech_timeout='auto'
        )
        
    else:
        # גם כאן מעבר לאנגלית
        response.say("Sorry, I did not hear you. Please repeat.", language='en-US')
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            speech_timeout='auto'
        )

    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
