from flask import Flask, request, Response
from dotenv import load_dotenv
from google.cloud import texttospeech
from google.oauth2 import service_account
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai
import os
import json
import uuid
from urllib.parse import urljoin
print("🚀 Flask app is loading...")


# Load local .env if running locally (ignored on Railway)
load_dotenv(dotenv_path='env/.env')

# Init Flask
app = Flask(__name__)

# OpenAI API Key
openai.api_key = os.getenv("OPENAI_API_KEY")

# === Helper: Create TTS client on demand === #
def get_tts_client():
    google_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not google_creds_json:
        raise RuntimeError("Missing GOOGLE_APPLICATION_CREDENTIALS_JSON environment variable.")
    
    try:
        credentials_dict = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return texttospeech.TextToSpeechClient(credentials=credentials)
    except Exception as e:
        print("❌ Google TTS init failed:", e)
        raise


# === Routes === #

@app.route("/twilio/answer", methods=["POST"])
def twilio_answer():
    """Initial webhook when call connects"""
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


@app.route("/twilio/process", methods=["POST"])
def twilio_process():
    """Process speech input and respond with audio"""
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

    gpt_response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": user_input}],
        max_tokens=100,
        temperature=0.7
    )
    bot_text = gpt_response.choices[0].message.content
    print("🤖 תשובת GPT:", bot_text)

    # Synthesize speech with Google TTS (WAV)
    tts_client = get_tts_client()
    synthesis_input = texttospeech.SynthesisInput(text=bot_text)
    voice = texttospeech.VoiceSelectionParams(
        language_code="he-IL",
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=8000  # good for telephony
    )

    response_tts = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    # Save WAV file with unique name
    unique_id = str(uuid.uuid4())
    output_path = f"static/output_{unique_id}.wav"
    with open(output_path, "wb") as out:
        out.write(response_tts.audio_content)

    # TwiML response
    response = VoiceResponse()
    wav_url = urljoin(request.host_url, f"static/output_{unique_id}.wav")
    response.play(wav_url)

    # Follow-up question
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


@app.route("/", methods=["GET"])
def index():
    return "✅ Flask server is running on Railway!"


# === Run App === #
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
