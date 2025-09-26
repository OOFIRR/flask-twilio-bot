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
import traceback
import threading
import time

print("🚀 Flask app is loading...")

# Load .env
load_dotenv(dotenv_path='env/.env')

app = Flask(__name__)

# In-memory session context
session_memory = {}

# API keys
print("🔑 Checking env variables...")
openai.api_key = os.getenv("OPENAI_API_KEY")
if openai.api_key:
    print("OPENAI_API_KEY loaded: ✅")
else:
    print("❌ Missing OPENAI_API_KEY")

google_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if google_creds_json:
    print(f"GOOGLE_APPLICATION_CREDENTIALS_JSON length: {len(google_creds_json)}")
else:
    print("❌ Missing GOOGLE_APPLICATION_CREDENTIALS_JSON")


def get_tts_client():
    try:
        credentials_dict = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return texttospeech.TextToSpeechClient(credentials=credentials)
    except Exception as e:
        print("❌ Google TTS init failed:", e)
        raise


def delete_file_later(path, delay=30):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
            print(f"🗑️ Deleted file: {path}")
        except Exception as e:
            print(f"❌ Failed to delete file {path}:", e)
    threading.Thread(target=_delete).start()


@app.route("/", methods=["GET"])
def index():
    print("✅ GET / called")
    return "✅ Flask server is running on Railway!"


@app.route("/twilio/answer", methods=["POST"])
def twilio_answer():
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
        gather.say("שלום! איך אפשר לעזור לך היום?", language='he-IL')
        response.append(gather)
        response.say("לא קיבלתי תשובה. להתראות!", language='he-IL')

        xml_str = str(response)
        return Response(xml_str, status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

    except Exception as e:
        print("❌ ERROR in /twilio/answer:", e)
        traceback.print_exc()
        return Response("Internal Server Error", status=500)


@app.route("/twilio/process", methods=["POST"])
def twilio_process():
    try:
        print("🛠️ Request to /twilio/process")
        user_input = request.form.get('SpeechResult')
        call_sid = request.form.get('CallSid')

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

        # Load session context
        messages = session_memory.get(call_sid, [])
        messages.append({"role": "user", "content": user_input})

        gpt_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=150,
            temperature=0.7
        )
        bot_text = gpt_response.choices[0].message.content.strip()
        print("🤖 GPT says:", bot_text)

        # Update session memory
        messages.append({"role": "assistant", "content": bot_text})
        session_memory[call_sid] = messages

        # Generate TTS
        tts_client = get_tts_client()
        synthesis_input = texttospeech.SynthesisInput(text=bot_text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="he-IL",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=8000
        )
        response_tts = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        # Save WAV
        unique_id = str(uuid.uuid4())
        output_path = f"static/output_{unique_id}.wav"
        with open(output_path, "wb") as out:
            out.write(response_tts.audio_content)
        delete_file_later(output_path, delay=30)

        # URL to play
        wav_url = urljoin(request.host_url, f"static/output_{unique_id}.wav")
        print(f"🔊 Playing audio: {wav_url}")

        # Build TwiML response
        response = VoiceResponse()
        response.play(wav_url)

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
        response = VoiceResponse()
        response.say("אירעה שגיאה. נסה שוב מאוחר יותר.", language='he-IL')
        return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
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
import traceback
import threading
import time

print("🚀 Flask app is loading...")

# Load .env
load_dotenv(dotenv_path='env/.env')

app = Flask(__name__)

# In-memory session context
session_memory = {}

# API keys
print("🔑 Checking env variables...")
openai.api_key = os.getenv("OPENAI_API_KEY")
if openai.api_key:
    print("OPENAI_API_KEY loaded: ✅")
else:
    print("❌ Missing OPENAI_API_KEY")

google_creds_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
if google_creds_json:
    print(f"GOOGLE_APPLICATION_CREDENTIALS_JSON length: {len(google_creds_json)}")
else:
    print("❌ Missing GOOGLE_APPLICATION_CREDENTIALS_JSON")


def get_tts_client():
    try:
        credentials_dict = json.loads(google_creds_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return texttospeech.TextToSpeechClient(credentials=credentials)
    except Exception as e:
        print("❌ Google TTS init failed:", e)
        raise


def delete_file_later(path, delay=30):
    def _delete():
        time.sleep(delay)
        try:
            os.remove(path)
            print(f"🗑️ Deleted file: {path}")
        except Exception as e:
            print(f"❌ Failed to delete file {path}:", e)
    threading.Thread(target=_delete).start()


@app.route("/", methods=["GET"])
def index():
    print("✅ GET / called")
    return "✅ Flask server is running on Railway!"


@app.route("/twilio/answer", methods=["POST"])
def twilio_answer():
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
        gather.say("שלום! איך אפשר לעזור לך היום?", language='he-IL')
        response.append(gather)
        response.say("לא קיבלתי תשובה. להתראות!", language='he-IL')

        xml_str = str(response)
        return Response(xml_str, status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})

    except Exception as e:
        print("❌ ERROR in /twilio/answer:", e)
        traceback.print_exc()
        return Response("Internal Server Error", status=500)


@app.route("/twilio/process", methods=["POST"])
def twilio_process():
    try:
        print("🛠️ Request to /twilio/process")
        user_input = request.form.get('SpeechResult')
        call_sid = request.form.get('CallSid')

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

        # Load session context
        messages = session_memory.get(call_sid, [])
        messages.append({"role": "user", "content": user_input})

        gpt_response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=150,
            temperature=0.7
        )
        bot_text = gpt_response.choices[0].message.content.strip()
        print("🤖 GPT says:", bot_text)

        # Update session memory
        messages.append({"role": "assistant", "content": bot_text})
        session_memory[call_sid] = messages

        # Generate TTS
        tts_client = get_tts_client()
        synthesis_input = texttospeech.SynthesisInput(text=bot_text)
        voice = texttospeech.VoiceSelectionParams(
            language_code="he-IL",
            ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=8000
        )
        response_tts = tts_client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config
        )

        # Save WAV
        unique_id = str(uuid.uuid4())
        output_path = f"static/output_{unique_id}.wav"
        with open(output_path, "wb") as out:
            out.write(response_tts.audio_content)
        delete_file_later(output_path, delay=30)

        # URL to play
        wav_url = urljoin(request.host_url, f"static/output_{unique_id}.wav")
        print(f"🔊 Playing audio: {wav_url}")

        # Build TwiML response
        response = VoiceResponse()
        response.play(wav_url)

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
        response = VoiceResponse()
        response.say("אירעה שגיאה. נסה שוב מאוחר יותר.", language='he-IL')
        return Response(str(response), status=200, mimetype='application/xml', headers={"Content-Type": "text/xml"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
