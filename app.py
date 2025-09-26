from flask import Flask, request, Response, make_response
from dotenv import load_dotenv
from google.cloud import texttospeech
from google.oauth2 import service_account
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai
import os
import json
import uuid
from urllib.parse import urljoin
import sys
import traceback
print("📥 Request to /twilio/answer, method:", request.method)

print("🚀 Flask app is loading...")

# Load local .env file (for local dev only)
load_dotenv(dotenv_path='env/.env')

# === Debug: Check env variables === #
print("🔑 Checking env variables...")
openai_key = os.getenv("OPENAI_API_KEY")
google_creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

print("OPENAI_API_KEY loaded:", "✅" if openai_key else "❌ MISSING")
print("GOOGLE_APPLICATION_CREDENTIALS_JSON length:", len(google_creds) if google_creds else "❌ MISSING")

# Init Flask
app = Flask(__name__)
print("📡 Flask app is live and routes are registered.")

# === Global error handler === #
@app.errorhandler(Exception)
def handle_exception(e):
    print("❌ Unhandled Exception:", e)
    traceback.print_exc(file=sys.stdout)
    return "Internal Server Error", 500

# Set OpenAI API Key
openai.api_key = openai_key

# === Helper: Create Google TTS client === #
def get_tts_client():
    if not google_creds:
        raise RuntimeError("Missing GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable.")
    try:
        credentials_dict = json.loads(google_creds)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return texttospeech.TextToSpeechClient(credentials=credentials)
    except Exception as e:
        print("❌ Google TTS init failed:", e)
        raise

# === Route: Health check === #
@app.route("/", methods=["GET"])
def index():
    try:
        print("✅ GET / called")
        response = make_response("✅ Flask server is running on Railway!", 200)
        response.mimetype = "text/plain"
        return response
    except Exception as e:
        print("❌ Index Error:", e)
        return "Internal Error", 500

# === Route: Twilio call entry === #
@app.route("/twilio/answer", methods=["GET", "POST", "OPTIONS"])
def twilio_answer():
    if request.method == "OPTIONS":
        print("🔧 Received OPTIONS request on /twilio/answer")
        return Response(status=200)

    if request.method == "GET":
        print("🌐 GET request to /twilio/answer — not allowed for Twilio")
        return make_response("This endpoint expects POST requests from Twilio.", 200)

    print("📞 שיחה נכנסה /twilio/answer")

    response = VoiceResponse()
    gather = Gather(
        input='speech',
        action='/twilio/process',
        method='POST',
        language='he-IL',
        speech_timeout='auto'
    )
    gather.say("שלום! איך אפשר לעזור לך היום?", language='he-IL')
    response.append(gather)

    response.say("לא קיבלתי תשובה. להתראות!", language='he-IL')
    return Response(str(response), mimetype='application/xml')

# === Route: Process speech input === #
@app.route("/twilio/process", methods=["POST"])
def twilio_process():
    print("🛠️ בקשה ל־/twilio/process")

    user_input = request.form.get('SpeechResult')
    if not user_input:
        print("⚠️ לא התקבלה תשובה מהמשתמש.")
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
        return Response(str(response), mimetype='application/xml')

    print("🗣️ המשתמש אמר:", user_input)

    # --- GPT response --- #
    try:
        gpt_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": user_input}],
            max_tokens=100,
            temperature=0.7
        )
        bot_text = gpt_response.choices[0].message.content
        print("🤖 תשובת GPT:", bot_text)
    except Exception as e:
        print("❌ GPT ERROR:", e)
        response = VoiceResponse()
        response.say("אירעה שגיאה עם המערכת. נסה שוב מאוחר יותר.", language='he-IL')
        return Response(str(response), mimetype='application/xml')

    # --- Google TTS --- #
    try:
        tts_client = get_tts_client()
        synthesis_input = texttospeech.SynthesisInput(text=bot_text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="he-IL",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=8000  # for telephony
        )
        response_tts = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        unique_id = str(uuid.uuid4())
        output_path = f"static/output_{unique_id}.wav"
        with open(output_path, "wb") as out:
            out.write(response_tts.audio_content)

        wav_url = urljoin(request.host_url, f"static/output_{unique_id}.wav")
        print(f"🔊 Playing audio: {wav_url}")

        # Build response
        response = VoiceResponse()
        response.play(wav_url)

        # Follow-up
        gather = Gather(
            input='speech',
            action='/twilio/process',
            method='POST',
            language='he-IL',
            speech_timeout='auto'
        )
        gather.say("יש לך שאלה נוספת?", language='he-IL')
        response.append(gather)

        return Response(str(response), mimetype='application/xml')

    except Exception as e:
        print("❌ Google TTS ERROR:", e)
        response = VoiceResponse()
        response.say("אירעה שגיאה ביצירת השמע.", language='he-IL')
        return Response(str(response), mimetype='application/xml')
