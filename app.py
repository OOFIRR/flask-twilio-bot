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

# *** תיקון קריטי למהירות: שימוש בקול STANDARD המהיר (במקום WaveNet האיטי) ***
# Twilio ממליץ על Standard לקבלת Latency נמוך בשיחות בזמן אמת.
LLM_HEBREW_VOICE = "Google.he-IL-Standard-A" 

# --- פונקציית LLM ---
def call_llm_api(prompt):
    """
    מתקשרת ל-OpenAI API (GPT-3.5) לקבלת תשובה.
    """
    if not OPENAI_API_KEY:
        return "אני מצטער, אבל מודל השפה כרגע אינו זמין. חסר מפתח OpenAI."

    messages = [
        # הוראה להיות קצר ותמציתי לקיצור זמן התגובה
        {"role": "system", "content": "אתה בוט טלפוני בעברית. ענה בקצרה (ב-2-3 משפטים מקסימום), בתמציתיות ובטון חברותי וממוקד, כאילו אתה מדבר בטלפון."},
        {"role": "user", "content": prompt}
    ]

    try:
        api_url = "https://api.openai.com/v1/chat/completions"
        
        payload = {
            "model": "gpt-3.5-turbo", 
            "messages": messages,
            "max_tokens": 100, # מקסימום 100 טוקנים כדי לקצר TTS
            "temperature": 0.7
        }

        response = requests.post(
            api_url, 
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {OPENAI_API_KEY}' 
            }, 
            json=payload
        )
        response.raise_for_status()

        result = response.json()
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
    
    # שימוש בקול Standard כדי להבטיח שהוא יעבור
    print(f"Using Standard voice ({LLM_HEBREW_VOICE}) for initial prompt.")
    response.say(initial_prompt, language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE) 

    # *** הגדרות Gather מחמירות ליציבות ומהירות ***
    response.gather(
        input='speech',
        action='/handle_speech',
        language=HEBREW_LANGUAGE_CODE,
        timeout='7',           # זמן המתנה כולל ארוך יותר
        speechTimeout='2'      # סיום האזנה 2 שניות אחרי הפסקה (מומלץ ולא auto)
    )
    
    return str(response)

# --- Webhook לטיפול בקלט קולי ---
@app.route("/handle_speech", methods=['POST'])
def handle_speech():
    """
    מקבל את הדיבור של המשתמש, שולח ל-LLM ומחזיר את התשובה.
    """
    response = VoiceResponse()
    # Twilio שולח את התוצאה ל-SpeechResult
    spoken_text = request.form.get('SpeechResult') 
    
    # *** התחלת הלולאה ***
    if spoken_text:
        print(f"User said: {spoken_text}")
        
        # קריאה ל-LLM
        llm_response_text = call_llm_api(spoken_text)
        print(f"LLM response: {llm_response_text}")

        # שימוש בקול Standard המהיר עבור התשובה
        print(f"Using fast Google Standard Hebrew voice ({LLM_HEBREW_VOICE}) for LLM response.")
        response.say(llm_response_text, language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)

        # איסוף קלט נוסף כדי להמשיך את השיחה (חזרה ל-/handle_speech)
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            timeout='7',
            speechTimeout='2'
        )
        
    else:
        # אם לא התקבל קלט (כי המשתמש שתק או השיחה נותקה מוקדם)
        response.say("לא שמעתי אותך. אנא נסה לומר משהו שוב.", language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)
        # מחזירים את הפונקציה לתחילת הלולאה
        response.gather(
            input='speech',
            action='/handle_speech',
            language=HEBREW_LANGUAGE_CODE,
            timeout='7',
            speechTimeout='2'
        )
    
    # *** תיקון קריטי: הסרת הניתוק הגורף (response.hangup()) מחוץ לבלוק ה-if. ***
    # כל עוד ה-TwiML תקין, ה-Gather תמיד יופעל שוב, והשיחה לא תנותק.
    
    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
