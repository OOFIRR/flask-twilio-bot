import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
HEBREW_LANGUAGE_CODE = "he-IL" 

# *** שינוי קריטי לשיפור מהירות (Latency): שימוש בקול WaveNet ***
# Google.he-IL-Wavenet-A הוא הקול המהיר ביותר לייצור (Low Latency TTS).
LLM_HEBREW_VOICE = "Google.he-IL-Wavenet-A" 
# קול ברירת מחדל יציב של Twilio עבור הודעת הפתיחה בלבד
TWILIO_DEFAULT_VOICE = "woman" 

# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-OpenAI API (GPT-3.5) לקבלת תשובה.
    """
    if not OPENAI_API_KEY:
        # הודעת שגיאה זו תושמע על ידי ה-Say fallback של Twilio.
        return "אני מצטער, אבל מודל השפה כרגע אינו זמין. חסר מפתח OpenAI."

    # הגדרת השיחה
    messages = [
        # *** שינוי: הוספת הוראה להיות קצר מאוד ***
        {"role": "system", "content": "אתה בוט טלפוני בעברית. ענה בקצרה, בתמציתיות ובטון חברותי וממוקד, כאילו אתה מדבר בטלפון."},
        {"role": "user", "content": prompt}
    ]

    try:
        # בניית ה-URL וה-Payload לקריאה ל-OpenAI API
        api_url = "https://api.openai.com/v1/chat/completions"
        
        payload = {
            "model": "gpt-3.5-turbo", 
            "messages": messages,
            # *** שינוי: קיצור אורך התשובה המקסימלי ל-100 טוקנים ***
            "max_tokens": 100,
            "temperature": 0.7
        }

        # ביצוע הקריאה ל-API
        response = requests.post(
            api_url, 
            headers={
                'Content-Type': 'application/json',
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
    
    # שימוש בקול ברירת המחדל היציב (woman) רק עבור הפתיחה
    print(f"Using Twilio DEFAULT voice ({TWILIO_DEFAULT_VOICE}) for initial prompt to prevent crash.")
    response.say(initial_prompt, language=HEBREW_LANGUAGE_CODE, voice=TWILIO_DEFAULT_VOICE) 

    # בקשה לאיסוף הקלט הקולי של המשתמש (Gather)
    response.gather(
        input='speech',
        action='/handle_speech',
        language=HEBREW_LANGUAGE_CODE,
        # *** שינוי קריטי: קיצור זמן ההמתנה לקלט קולי ***
        speechTimeout='2'
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

        # שימוש בקול WaveNet המהיר
        print(f"Using fast Google WaveNet Hebrew voice ({LLM_HEBREW_VOICE}) for LLM response.")
        response.say(llm_response_text, language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)

        # איסוף קלט נוסף כדי להמשיך את השיחה (לולאה)
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            # *** שינוי קריטי: קיצור זמן ההמתנה לקלט קולי ***
            speechTimeout='2' 
        )
        
    else:
        # הודעת שגיאה במקרה של חוסר קלט
        response.say("לא שמעתי אותך. תוכל לחזור על דבריך?", language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            speechTimeout='2'
        )

    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
