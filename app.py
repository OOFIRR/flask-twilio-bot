# -*- coding: utf-8 -*-
import os
import time
import requests
import json
import uuid
import tempfile
from flask import Flask, request, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather

# ייבוא תקני לפי גרסה עדכנית של elevenlabs
from elevenlabs.client import ElevenLabs
from elevenlabs import save
from elevenlabs.api.error import APIError

# --- 1. הגדרות וטעינת מפתחות API ---

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
HEBREW_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "MTfTLiL7VOpWnuOQqiyV")

ELEVENLABS_CLIENT = None

if ELEVENLABS_API_KEY:
    try:
        ELEVENLABS_CLIENT = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        print("ElevenLabs API key and client successfully loaded.")
    except Exception as e:
        print(f"Error setting ElevenLabs API key. TTS will use Twilio Say fallback. Error: {e}")

CALL_CONTEXT = {}

# --- 2. הגדרות שרת Flask ---
app = Flask(__name__, static_url_path='/static', static_folder='static')

# --- 3. פונקציות עזר ---

def call_llm_api(prompt, call_sid):
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY is not set. Cannot call LLM.")
        return "אני מצטער, מודל השפה אינו זמין כרגע."

    history = CALL_CONTEXT.get(call_sid, [])
    
    history.append({
        "role": "user",
        "parts": [{"text": prompt}]
    })

    system_instruction = {
        "parts": [{"text": "אתה עוזר קולי בעברית שמספק תשובות קצרות ותכליתיות. ענה בצורה ידידותית, קצרה וטבעית. אם המשתמש שואל שאלות מורכבות מדי, בקש ממנו לשאול שאלות פשוטות יותר."}]
    }

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-05-20:generateContent?key={OPENAI_API_KEY}"
    
    payload = {
        "contents": history,
        "systemInstruction": system_instruction,
    }

    try:
        max_retries = 3
        for attempt in range(max_retries):
            response = requests.post(api_url, headers={'Content-Type': 'application/json'}, json=payload)
            
            if response.status_code == 429 and attempt < max_retries - 1:
                wait_time = 2 ** attempt
                time.sleep(wait_time)
                continue

            response.raise_for_status()
            break
        else:
            response.raise_for_status()

        result = response.json()
        candidate = result.get('candidates', [{}])[0]
        text_response = candidate.get('content', {}).get('parts', [{}])[0].get('text', "מודל השפה לא הצליח להגיב.")

        history.append({
            "role": "model",
            "parts": [{"text": text_response}]
        })
        CALL_CONTEXT[call_sid] = history
        return text_response

    except requests.exceptions.RequestException as e:
        print(f"Error calling LLM API (Status: {response.status_code if 'response' in locals() else 'Unknown'}): {e}")
        return "אני מצטער, חלה שגיאה בחיבור למודל השפה. אנא נסה שוב."


def generate_tts_wav(text_to_speak, call_sid):
    if not ELEVENLABS_CLIENT:
        print("ElevenLabs client not initialized. Falling back to Twilio Say.")
        return None
        
    try:
        filename = f"{call_sid}_{int(time.time())}.wav"
        filepath = os.path.join(app.static_folder, filename)
        
        audio = ELEVENLABS_CLIENT.generate(
            text=text_to_speak,
            voice=HEBREW_VOICE_ID,
            model="eleven_multilingual_v2" 
        )
        
        save(audio, filepath)
        static_url = f"/static/{filename}"
        return static_url

    except APIError as e:
        print(f"ElevenLabs API Error: {e}. Falling back to Twilio Say.")
        return None
    except Exception as e:
        print(f"General TTS Error: {e}. Falling back to Twilio Say. Error: {e}")
        return None
    
    
def cleanup_wav_file(filepath):
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            print(f"Cleaned up file: {filepath}")
    except OSError as e:
        print(f"Error cleaning up file {filepath}: {e}")

# --- 4. נתיבי Webhook ---

@app.route("/twilio/answer", methods=['POST'])
def answer_call():
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')
    
    welcome_text = "שלום! איך אפשר לעזור לך היום?"
    wav_url = generate_tts_wav(welcome_text, call_sid)

    if wav_url:
        resp.play(url=request.url_root + wav_url)
    else:
        resp.say(welcome_text, voice='Polly.Amy', language='he-IL')

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

@app.route("/twilio/handle_speech", methods=['POST'])
def handle_speech():
    resp = VoiceResponse()
    call_sid = request.values.get('CallSid')
    user_speech = request.values.get('SpeechResult')
    
    if user_speech is None or user_speech.strip() == "":
        if call_sid in CALL_CONTEXT:
            del CALL_CONTEXT[call_sid]
        
        repeat_text = "לא שמעתי אותך בבירור. נסה שוב."
        wav_url = generate_tts_wav(repeat_text, call_sid)
        
        if wav_url:
            resp.play(url=request.url_root + wav_url)
        else:
            resp.say(repeat_text, voice='Polly.Amy', language='he-IL')

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

    llm_response = call_llm_api(user_speech, call_sid)
    wav_url = generate_tts_wav(llm_response, call_sid)
    
    if wav_url:
        resp.play(url=request.url_root + wav_url)
        cleanup_wav_file(os.path.join(app.static_folder, os.path.basename(wav_url)))
    else:
        resp.say(llm_response, voice='Polly.Amy', language='he-IL')

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

# --- 5. בדיקת בריאות ---

@app.route("/")
def health_check():
    return "Flask server is running on Railway and ready for Twilio calls!"

# --- 6. הרצת השרת ---

if __name__ == "__main__":
    if not os.path.exists(app.static_folder):
        os.makedirs(app.static_folder)
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
