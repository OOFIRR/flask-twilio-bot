import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
# ***שינוי קריטי: טוען את המפתח כ-OPENAI_API_KEY***
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
HEBREW_LANGUAGE_CODE = "he-IL" 

# נשתמש בקול אנגלי (Polly.Salli) שהוא יציב יותר ב-Twilio,
# וניתן לו לדבר את הטקסט העברי. זה יישמע במבטא זר, אבל ימנע התנתקות.
HEBREW_VOICE = "Polly.Salli" 

# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-OpenAI API (GPT-3.5) לקבלת תשובה.
    """
    if not OPENAI_API_KEY:
        return "אני מצטער, אבל מודל השפה כרגע אינו זמין. חסר מפתח OpenAI."

    # הגדרת השיחה
    messages = [
        {"role": "system", "content": "אתה בוט טלפוני בעברית, חברותי, ענייני וממוקד. ענה בקצרה, בטון קול טבעי, כאילו אתה מדבר בטלפון."},
        {"role": "user", "content": prompt}
    ]

    try:
        # בניית ה-URL וה-Payload לקריאה ל-OpenAI API
        api_url = "https://api.openai.com/v1/chat/completions"
        
        payload = {
            "model": "gpt-3.5-turbo", # מודל יציב ומהיר
            "messages": messages,
            "max_tokens": 150,
            "temperature": 0.7
        }

        # ביצוע הקריאה ל-API
        response = requests.post(
            api_url, 
            headers={
                'Content-Type': 'application/json',
                # שימוש במפתח OpenAI
                'Authorization': f'Bearer {OPENAI_API_KEY}' 
            }, 
            json=payload
        )
        response.raise_for_status() # זורק שגיאה אם הסטטוס אינו 2xx

        result = response.json()
        # חילוץ התשובה
        text_response = result['choices'][0]['message']['content']
        return text_response.strip()

    except Exception as e:
        print(f"OpenAI API Call Failed: {e}")
        return "אני מצטער, חלה תקלה בשירות השפה. אנא נסה שוב."


# --- Webhook לטיפול בשיחה נכנסת (התחלה) ---
@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    נקודת הכניסה לשיחת הטלפון. מחזירה TwiML עם בקשה לקלט קולי.
    """
    response = VoiceResponse()
    
    initial_prompt = "שלום, הגעת לבוט הטלפוני. איך אוכל לעזור לך היום?"
    
    print("Using Twilio default Say with non-Hebrew, stable voice (Polly.Salli).")
    # שימוש בקול יציב (Salli) עבור הקול, ושמירה על הגדרת השפה העברית עבור זיהוי דיבור.
    response.say(initial_prompt, language=HEBREW_LANGUAGE_CODE, voice=HEBREW_VOICE) 

    # בקשה לאיסוף הקלט הקולי של המשתמש (Gather)
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

        # שימוש ב-Say המובנה של Twilio
        print("Using Twilio default Say with non-Hebrew, stable voice (Polly.Salli).")
        response.say(llm_response_text, language=HEBREW_LANGUAGE_CODE, voice=HEBREW_VOICE)

        # איסוף קלט נוסף כדי להמשיך את השיחה (לולאה)
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            speech_timeout='auto'
        )
        
    else:
        # הודעת שגיאה במקרה של חוסר קלט
        response.say("לא שמעתי אותך. תוכל לחזור על דבריך?", language=HEBREW_LANGUAGE_CODE, voice=HEBREW_VOICE)
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
