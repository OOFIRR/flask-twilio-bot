from flask import Flask, request, Response
from dotenv import load_dotenv
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai
import os
import json
import uuid
from urllib.parse import urljoin
import traceback
import threading
import time

print("🚀 Flask app is loading...")

# --- Global Initialization and Error Handling ---

# Load environment variables (from .env file if running locally)
load_dotenv(dotenv_path='env/.env')

app = Flask(__name__)

# In-memory session context for conversation history
session_memory = {}

# API keys and credentials (loaded from Railway environment variables)
openai.api_key = os.getenv("OPENAI_API_KEY")
google_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

TTS_AVAILABLE = False
try:
    # Attempt to import Google TTS libraries
    from google.cloud import texttospeech
    from google.oauth2 import service_account
    TTS_AVAILABLE = True
    print("Google TTS libraries loaded successfully. ✅")
except ImportError as e:
    print(f"❌ Google TTS IMPORT FAILED: {e}. Falling back to Twilio TTS.")
except Exception as e:
    # Catches issues with loading credentials or other config problems
    print(f"❌ Google TTS IMPORT FAILED due to a configuration error: {e}. Falling back to Twilio TTS.")


# Ensure the static directory exists for WAV files
STATIC_DIR = os.path.join(os.getcwd(), 'static')
try:
    os.makedirs(STATIC_DIR, exist_ok=True)
    print(f"📁 Ensured static directory exists at: {STATIC_DIR}")
except Exception as e:
    print(f"❌ Failed to ensure static directory exists: {e}")


# --- Helper Functions ---

def get_tts_client():
    """Initializes and returns the Google Text-to-Speech client."""
    if not TTS_AVAILABLE:
        raise EnvironmentError("Google TTS is not available.")
        
    if not google_creds_json:
        # This shouldn't happen if GOOGLE_APPLICATION_CREDENTIALS_JSON is set on Railway
        raise EnvironmentError("Missing Google TTS credentials (JSON string).")
        
    try:
        credentials_dict = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return texttospeech.TextToSpeechClient(credentials=credentials)
    except Exception as e:
        print(f"❌ Google TTS init failed during client creation: {e}")
        raise


def delete_file_later(path, delay=30):
    """Deletes a file asynchronously after a delay."""
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
            print(f"🗑️ Deleted file: {path}")
        except Exception as e:
            print(f"❌ Failed to delete file {path}:", e)
    
    # Start the deletion process in a new thread
    threading.Thread(target=_delete).start()


# --- Flask Routes ---

@app.route("/", methods=["GET"])
def index():
    """Health Check Route."""
    print("✅ GET / called")
    return "✅ Flask server is running on Railway and ready for Twilio calls!"


@app.route("/twilio/answer", methods=["POST"])
def twilio_answer():
    """Entry point for a new call from Twilio. Uses Twilio Say for stability."""
    try:
        print("📞 New call: /twilio/answer")
        
        response = VoiceResponse()

        gather = Gather(
            input='speech',
            action='/twilio/process',
            method='POST',
            language='he-IL',
            speech_timeout='auto'
        )
        
        # *** CRITICAL FIX: Use Twilio's built-in TTS (<Say>) for the initial prompt ***
        # This bypasses the static file access issue observed on Railway for the first prompt.
        gather.say("שלום! איך אפשר לעזור לך היום?", language='he-IL')
        
        response.append(gather)
        
        # If the user doesn't respond
        response.say("לא קיבלתי תשובה. להתראות!", language='he-IL')

        xml_str = str(response)
        return Response(xml_str, status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

    except Exception as e:
        print("❌ ERROR in /twilio/answer:", e)
        traceback.print_exc()
        return Response("Internal Server Error", status=500)


@app.route("/twilio/process", methods=["POST"])
def twilio_process():
    """Handles speech input, queries GPT, generates Google TTS audio, and continues the call."""
    try:
        print("🛠️ Request to /twilio/process")
        
        user_input = request.form.get('SpeechResult')
        call_sid = request.form.get('CallSid')

        # --- Handle Missing Input ---
        if not user_input:
            print("⚠️ No speech input")
            response = VoiceResponse()
            response.say("לא שמעתי אותך. תוכל לנסות שוב?", language='he-IL')
            gather = Gather(
                input='speech',
                action='/twilio/process',
                method='POST',
                language='he-IL',
                speech_timeout='auto'
            )
            gather.say("מה תרצה לדעת?", language='he-IL')
            response.append(gather)
            return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

        print(f"📞 CallSid: {call_sid}")
        print("🗣️ User said:", user_input)

        # --- Session Management (Conversation Memory) ---
        messages = session_memory.get(call_sid, [])
        
        # System instruction for Hebrew
        if not messages:
             messages.append({"role": "system", "content": "אתה עוזר וירטואלי שמנהל שיחת טלפון ידידותית בעברית. השב בקצרה, בבהירות ובטון טבעי."})

        messages.append({"role": "user", "content": user_input})
        
        # --- OpenAI Call ---
        gpt_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=150,
            temperature=0.7
        )
        bot_text = gpt_response.choices[0].message.content.strip()
        print("🤖 GPT says:", bot_text)

        # Update conversation memory
        messages.append({"role": "assistant", "content": bot_text})
        session_memory[call_sid] = messages
        
        # --- Create TwiML Response ---
        response = VoiceResponse()

        if TTS_AVAILABLE:
             # --- Generate Audio (Google TTS) ---
            try:
                tts_client = get_tts_client()
                synthesis_input = texttospeech.SynthesisInput(text=bot_text)
                voice = texttospeech.VoiceSelectionParams(
                    language_code="he-IL",
                    ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
                )
                # Important: Twilio requires 8000Hz PCM 16-bit
                audio_config = texttospeech.AudioConfig(
                    audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                    sample_rate_hertz=8000 
                )
                response_tts = tts_client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice,
                    audio_config=audio_config
                )

                # --- Save File and Schedule Deletion ---
                unique_id = str(uuid.uuid4())
                output_path = os.path.join(STATIC_DIR, f"output_{unique_id}.wav") 
                with open(output_path, "wb") as out:
                    out.write(response_tts.audio_content)
                    
                delete_file_later(output_path, delay=30) 

                # Create public URL (Twilio will fetch this)
                wav_url = urljoin(request.host_url, f"static/output_{unique_id}.wav")
                print(f"🔊 Playing audio using Google TTS: {wav_url}")

                response.play(wav_url) 
            except Exception as e:
                print(f"❌ Google TTS runtime failed ({e}). Falling back to Twilio Say.")
                # Fallback to Twilio Say if Google TTS fails during runtime
                response.say(bot_text, language='he-IL')
        else:
             # Fallback to Twilio Say if Google TTS was never available
             print("🔊 Playing audio using Twilio Say (Google TTS not available).")
             response.say(bot_text, language='he-IL')

        # Continue the conversation loop
        gather = Gather(
            input='speech',
            action='/twilio/process',
            method='POST',
            language='he-IL',
            speech_timeout='auto'
        )
        gather.say("יש לך שאלה נוספת?", language='he-IL')
        response.append(gather)

        return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

    except Exception as e:
        print("❌ ERROR in /twilio/process:", e)
        traceback.print_exc()
        # Friendly error response
        response = VoiceResponse()
        response.say("אירעה שגיאה. נסה שוב מאוחר יותר.", language='he-IL')
        return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
