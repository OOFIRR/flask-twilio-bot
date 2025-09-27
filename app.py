import os
import requests
import json
from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Gather

# --- הגדרות כלליות ---
app = Flask(__name__)

# הגדרת משתני סביבה.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY") 
# *** תיקון קריטי: שימוש בקוד השפה המומלץ ל-Twilio TTS ***
HEBREW_LANGUAGE_CODE = "iw-IL" 

# *** שימוש בקול STANDARD המהיר (המלצת Twilio) ***
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
    נקודת הכניסה לשיחת הטלפון. מחזירה TwiML עם בקשה לקלט מקשים.
    """
    response = VoiceResponse()
    
    # *** שינוי הוראה: המשתמש צריך להקליד מספר ***
    initial_prompt = "שלום, הגעת לבוט הטלפוני. מכיוון שאנו עדיין לא תומכים בזיהוי דיבור בעברית, אנא הקלד מספר מ-1 עד 9 בכדי לשאול שאלה כללית. נגיב לך בקול."
    
    print(f"Using Standard voice ({LLM_HEBREW_VOICE}) for initial prompt.")
    response.say(initial_prompt, language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE) 

    # *** מעבר ל-DTMF: איסוף מקשים (Digits) ***
    response.gather(
        input='dtmf', # מקבל קלט מקשים במקום קול
        num_digits=1, # מצפה לקליטת ספרה אחת (1-9)
        action='/handle_speech',
        timeout='10' # זמן המתנה ארוך יותר
    )
    
    return str(response)

# --- Webhook לטיפול בקלט מקשים (DTMF) ---
@app.route("/handle_speech", methods=['POST'])
def handle_speech():
    """
    מקבל את המקשים של המשתמש, שולח ל-LLM ומחזיר את התשובה.
    """
    response = VoiceResponse()
    # *** שינוי קריטי: מקבל קלט מ-Digits במקום SpeechResult ***
    digits_result = request.form.get('Digits') 
    
    # המרת קלט המקשים לשאלה עבור ה-LLM
    if digits_result:
        # דוגמה לשאלה קבועה המבוססת על הקלט המספרי
        question_map = {
            '1': "מה מזג האוויר הצפוי למחר בתל אביב?",
            '2': "מהי הדרך הטובה ביותר ללמוד פייתון למתחילים?",
            '3': "מהם שלושה טיפים לשינה טובה?",
            # נוסיף שאלה ברירת מחדל אם הקליד מספר אחר
        }
        
        spoken_text = question_map.get(digits_result, f"הקשת את הספרה {digits_result}. אנא ענה על שאלה כללית בנושא טכנולוגיה.")

        print(f"User pressed: {digits_result}. Question sent to LLM: {spoken_text}")
        
        # קריאה ל-LLM
        llm_response_text = call_llm_api(spoken_text)
        print(f"LLM response: {llm_response_text}")

        # שימוש בקול Standard המהיר עבור התשובה
        print(f"Using fast Google Standard Hebrew voice ({LLM_HEBREW_VOICE}) for LLM response.")
        response.say(llm_response_text, language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)

        # איסוף קלט נוסף כדי להמשיך את השיחה (לולאה)
        # *** חוזר למצב קליטת DTMF ***
        response.say("אם ברצונך לשאול שאלה נוספת, אנא הקלד שוב מספר מ-1 עד 9.", language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)
        response.gather(
            input='dtmf',
            num_digits=1,
            action='/handle_speech',
            timeout='10'
        )
        
    else:
        # אם לא התקבל קלט, מנתקים
        response.say("לא התקבלה קליטה. להתראות.", language=HEBREW_LANGUAGE_CODE, voice=LLM_HEBREW_VOICE)
        response.hangup()
    
    return str(response)

# --- נקודת כניסה לשרת ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
