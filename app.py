import os
import io
import re
import time
import requests
from flask import Flask, request, url_for
from twilio.twiml.voice_response import VoiceResponse, Gather
from openai import OpenAI
from google.cloud import texttospeech
from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import DefaultCredentialsError
from elevenlabs import generate, set_api_key, save
from elevenlabs.api import Voices

# --- Configuration (Load Environment Variables) ---
# Note: Railway provides a $PORT variable automatically.
port = int(os.environ.get("PORT", 5000))
# The static_url_path is explicitly set to '/static' for Twilio to find the WAVs
app = Flask(__name__, static_folder='static', static_url_path='/static') 

# API Key Constants
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

# ElevenLabs Variables
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
# Example ID for a multilingual Hebrew voice, replace with your preferred voice ID
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "EXiS7oG68cK1F9bH5V8A") 

# --- Initialize Services ---
client = None
try:
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    print(f"Error initializing OpenAI client: {e}")

# Check Google TTS Credentials
try:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON"):
        print("Google TTS credentials found. ✅")
    else:
        print("Google TTS credentials missing.")
except Exception:
    print("Google TTS credentials check failed.")


# Check ElevenLabs Credentials
try:
    if ELEVENLABS_API_KEY:
        set_api_key(ELEVENLABS_API_KEY)
        print("ElevenLabs API key loaded successfully. ✅")
    else:
        print("ElevenLabs API key missing. Using Google TTS/Twilio Say as fallback.")
except Exception as e:
    print(f"Error setting ElevenLabs API key: {e}")

# Ensure the directory for static WAV files exists
STATIC_DIR = os.path.join(os.getcwd(), 'static')
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)
    print(f"📁 Ensured static directory exists at: {STATIC_DIR}")

# --- Helper Functions ---

# Function to generate TTS using ElevenLabs (Primary)
def generate_tts_elevenlabs(text, filename):
    """Generates audio using ElevenLabs and saves it to a WAV file."""
    print(f"Attempting ElevenLabs TTS (Primary) for: {text[:30]}...")
    
    if not ELEVENLABS_API_KEY:
        return False
        
    try:
        # Generate audio stream (audio is pcm data)
        audio = generate(
            text=text,
            voice=ELEVENLABS_VOICE_ID,
            model='eleven_multilingual_v2', # Supports Hebrew
            stream=False 
        )
        
        # Save the audio stream to the file
        filepath = os.path.join(STATIC_DIR, filename)
        # Note: ElevenLabs outputs MP3/MPEG audio by default, which Twilio supports.
        # However, to maintain the WAV file extension for consistency:
        with open(filepath, "wb") as f:
            f.write(audio)
        
        print(f"ElevenLabs TTS successful. Saved to {filepath}")
        return True
        
    except Exception as e:
        print(f"ElevenLabs TTS failed. Error: {e}")
        return False

# Function to generate TTS using Google Cloud TTS (Fallback 1)
def generate_tts_google(text, filename):
    """Generates audio using Google TTS and saves it to a WAV file."""
    print(f"Attempting Google TTS (Fallback) for: {text[:30]}...")
    try:
        tts_client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text=text)
        
        voice = texttospeech.VoiceSelectionParams(
            language_code="he-IL",
            name="he-IL-Wavenet-A" # A high-quality male voice
        )
        
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16, # Twilio prefers LINEAR16 or MP3
            sample_rate_hertz=8000 # 8000 Hz is required for Twilio phone calls
        )

        response = tts_client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )

        # The response's audio_content is binary.
        filepath = os.path.join(STATIC_DIR, filename)
        with open(filepath, "wb") as out:
            out.write(response.audio_content)
        print(f"Google TTS successful. Saved to {filepath}")
        return True
    
    except (DefaultCredentialsError, GoogleAPICallError) as e:
        print(f"Google TTS failed due to credentials or API call issue: {e}")
        return False
    except Exception as e:
        print(f"Google TTS failed unexpectedly: {e}")
        return False

# Master TTS function: Tries ElevenLabs, then Google TTS
def generate_tts_wav(text):
    """Generates a unique WAV/MP3 file and returns its URL, using ElevenLabs or Google TTS."""
    # Use a generic filename extension, Twilio can usually handle MP3/MPEG from ElevenLabs
    filename = f"output_{int(time.time())}_{os.getpid()}.wav" 
    
    # 1. Try ElevenLabs
    if ELEVENLABS_API_KEY and generate_tts_elevenlabs(text, filename):
        return url_for('static', filename=filename, _external=True)

    # 2. Try Google TTS (Fallback)
    if generate_tts_google(text, filename):
        return url_for('static', filename=filename, _external=True)
        
    # 3. Final failure
    print("All high-quality TTS options failed.")
    return None

# --- Application Logic (GPT and Memory) ---

# Simple in-memory history (CAUTION: Resets on server restart and not scalable)
conversation_history = {} 

def call_openai_with_memory(new_user_input, call_sid, client):
    """Handles the OpenAI API call, managing history."""
    
    global conversation_history
    history = conversation_history.get(call_sid, "")
    
    # 1. Define the system prompt
    system_prompt = (
        "את/ה בוט קולי בעברית, ידידותי, מתמצת ומהיר תגובה. "
        "אורך התשובה שלך **חייב להיות קצר מאוד** (משפט אחד או שניים מקסימום). "
        "ענה תמיד בעברית ודאג שהתשובה תהיה מנוקדת ומוכנה לקריאה קולית. "
        "זכור את ההיסטוריה ואל תחזור על עצמך. "
        "היסטוריה קודמת: " + history
    )
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": new_user_input}
            ],
            temperature=0.7,
            max_tokens=150
        )
        
        gpt_response = response.choices[0].message.content.strip()
        
        # 2. Update History
        new_history = f"אדם: {new_user_input}\nבוט: {gpt_response}\n"
        conversation_history[call_sid] = history + new_history
        
        return gpt_response
        
    except Exception as e:
        print(f"OpenAI API call failed: {e}")
        return "אני מצטער/ת, הייתה שגיאה בחיבור לשירותי הבינה המלאכותית. אנא נסה/י שוב."

# --- Twilio Routes ---

@app.route("/")
def home():
    """Simple health check endpoint."""
    return "Flask server is running on Railway and ready for Twilio calls! (ElevenLabs Ready)"

@app.route("/twilio/answer", methods=['POST'])
def twilio_answer():
    """Initial route when a call comes in. Prompts the user."""
    print("📞 New call: /twilio/answer")
    
    resp = VoiceResponse()
    
    # 1. Start Gathering Input (Speech Recognition)
    gather = Gather(
        input='speech',
        action=url_for('twilio_process', _external=True),
        language='he-IL', # Use he-IL for Hebrew Speech Recognition
        speech_timeout='auto',
        action_on_empty_result=False
    )
    
    # 2. The initial prompt (using Twilio Say as the most reliable method for the first turn)
    # We MUST use a supported language (en-US) for Twilio's Say to actually generate audio.
    gather.say('שלום! איך אפשר לעזור לך היום?', language='en-US') 
    
    resp.append(gather)
    
    # 3. Fallback if no speech is detected after the initial prompt
    resp.say("לא קיבלתי תשובה. להתראות!", language='en-US') 
    
    # Clean up old files 
    cleanup_old_wavs()
    
    return str(resp)


@app.route("/twilio/process", methods=['POST'])
def twilio_process():
    """Processes the user's speech input and generates a response."""
    
    user_speech = request.values.get('SpeechResult')
    call_sid = request.values.get('CallSid')
    
    print(f"👂 User said: {user_speech}")
    
    resp = VoiceResponse()
    
    if not user_speech:
        print("🤷‍♂️ No speech detected, hanging up.")
        resp.say("לא זיהיתי את דבריך. להתראות!", language='en-US')
        return str(resp)

    # 1. Get GPT Response
    gpt_text = call_openai_with_memory(user_speech, call_sid, client)
    print(f"🤖 GPT Response: {gpt_text}")

    # 2. Convert GPT Text to Speech (ElevenLabs > Google TTS > Twilio Say)
    wav_url = generate_tts_wav(gpt_text)
    
    if wav_url:
        # Use Twilio <Play> if a high-quality WAV file was successfully generated
        print(f"🔊 Playing high-quality WAV from: {wav_url}")
        resp.play(wav_url)
    else:
        # Fallback to Twilio Say if all high-quality TTS options failed
        print("🔊 Playing Twilio Say (Fallback).")
        resp.say(gpt_text, language='en-US') # MUST use en-US for Twilio Say

    # 3. Continue the conversation (Gather)
    gather = Gather(
        input='speech',
        action=url_for('twilio_process', _external=True),
        language='he-IL', # Use he-IL to enable Hebrew speech recognition
        speech_timeout='auto',
        action_on_empty_result=False
    )
    
    # Twilio plays the WAV/MP3 file first, and only then starts listening for the next turn.
    resp.append(gather)
    
    return str(resp)

# --- Cleanup ---

def cleanup_old_wavs(max_age_seconds=3600):
    """Deletes WAV files older than max_age_seconds."""
    now = time.time()
    for filename in os.listdir(STATIC_DIR):
        if filename.endswith(('.wav', '.mp3')):
            filepath = os.path.join(STATIC_DIR, filename)
            if os.path.getmtime(filepath) < now - max_age_seconds:
                os.remove(filepath)
                # print(f"🧹 Deleted old WAV: {filename}")


# --- Flask Run ---

if __name__ == "__main__":
    print(f"🚀 Flask app is loading...")
    # Clean up files on startup
    cleanup_old_wavs(max_age_seconds=0) 
    app.run(debug=True, host='0.0.0.0', port=port)
