import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
# OPENAI_API_KEY משמש עבור קריאות ל-Gemini API (דרך requests).
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
HEBREW_LANGUAGE_CODE = "he-IL" 

# נשתמש בקול מפורש של Amazon Polly (Polly.Amy) שתומך בשפות רבות 
# בניסיון לעקוף את בעיית ה-TTS העברי המובנה של Twilio
HEBREW_VOICE = "Polly.Amy" 

# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-Gemini API לקבלת תשובה.
    """
    if not OPENAI_API_KEY:
        return "אני מצטער, אבל מודל השפה כרגע אינו זמין."

    # הגדרת ההנחיה המערכתית (System Instruction)
    system_instruction = "אתה בוט טלפוני בעברית, חברותי, ענייני וממוקד. ענה בקצרה, בטון קול טבעי, כאילו אתה מדבר בטלפון."
    
    # בניית ההודעות
    messages = [
        {"role": "user", "content": prompt}
    ]

    try:
        # בניית ה-URL וה-Payload לקריאה ישירה ל-Gemini API
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={OPENAI_API_KEY}"
        
        # מבנה ה-contents כולל את היסטוריית השיחה (רק ההודעה הנוכחית במקרה זה)
        contents = [{"parts": [{"text": msg['content']}]} for msg in messages]
        
        payload = {
            "contents": contents,
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "config": {"maxOutputTokens": 150, "temperature": 0.7}
        }

        # ביצוע הקריאה ל-API
        response = requests.post(
            api_url, 
            headers={'Content-Type': 'application/json'}, 
            json=payload
        )
        response.raise_for_status() # זורק שגיאה אם הסטטוס אינו 2xx

        result = response.json()
        # חילוץ התשובה
        text_response = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', "מודל השפה לא הצליח להגיב.")
        return text_response.strip()

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
    
    initial_prompt = "שלום, הגעת לבוט הטלפוני. איך אוכל לעזור לך היום?"
    
    print("Using Twilio default Say with explicit Hebrew Voice (Polly.Amy).")
    # הפעלת TTS עם הקול המפורש
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
        print("Using Twilio default Say with explicit Hebrew Voice (Polly.Amy).")
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
