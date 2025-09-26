from flask import Flask, request, Response, send_file
from dotenv import load_dotenv
from google.cloud import texttospeech
import openai
import os
from twilio.twiml.voice_response import VoiceResponse, Gather

load_dotenv(dotenv_path='env/.env')

# Init Flask
app = Flask(__name__)

# Set your API keys
openai.api_key = os.getenv("OPENAI_API_KEY")
import json
from google.oauth2 import service_account

google_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")

credentials = service_account.Credentials.from_service_account_info(json.loads(google_creds_json))
tts_client = texttospeech.TextToSpeechClient(credentials=credentials)


# === ROUTES === #

@app.route("/twilio/answer", methods=["POST"])
def twilio_answer():
    """Initial webhook when call connects - plays a greeting and waits for input"""
    response = VoiceResponse()

    # Gather user speech input (language: Hebrew)
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
    """Process user's speech and return GPT response as audio"""
    # Get the transcription from Twilio's speech-to-text
    user_input = request.form.get('SpeechResult', '')

    print("User said:", user_input)  # Optional debug log

    # Call OpenAI for response
    gpt_response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": user_input}],
        max_tokens=100,
        temperature=0.7
    )
    bot_text = gpt_response.choices[0].message.content

    print("GPT Response:", bot_text)  # Optional debug log

    # Convert GPT response to Hebrew audio (output.mp3)
    tts_client = texttospeech.TextToSpeechClient()
    synthesis_input = texttospeech.SynthesisInput(text=bot_text)

    voice = texttospeech.VoiceSelectionParams(
        language_code="he-IL",
        ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
    )

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )

    response_tts = tts_client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    # Save audio to file
    with open("static/output.mp3", "wb") as out:
        out.write(response_tts.audio_content)

    # Respond with TwiML to play the audio
    response = VoiceResponse()
    response.play(url=request.host_url + 'static/output.mp3')

    # Optional: loop again to allow another input (basic example)
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


# === Optional health check ===
@app.route("/")
def index():
    return "Flask server is running!"


if __name__ == "__main__":
    app.run(debug=True, port=5000)
