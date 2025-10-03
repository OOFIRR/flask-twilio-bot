import os
import json
import base64
import logging
from flask import Flask, request
from flask_talisman import Talisman
from twilio.twiml.voice_response import VoiceResponse, Connect
from google.cloud import speech
from elevenlabs.client import ElevenLabs
from pydub import AudioSegment
from io import BytesIO

from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler

# --- Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration ---
GCP_CREDENTIALS_JSON = os.environ.get("GCP_CREDENTIALS_JSON")
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")

SAMPLE_RATE = 8000
LANGUAGE_CODE = "he-IL"

# --- App ---
app = Flask(__name__)
Talisman(app, content_security_policy=None)


# --- Google Credentials ---
def load_gcp_credentials():
    try:
        creds = json.loads(GCP_CREDENTIALS_JSON)
        with open("gcp_creds.json", "w") as f:
            json.dump(creds, f)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "gcp_creds.json"
        logger.info("GCP credentials loaded.")
    except Exception as e:
        logger.error(f"Error loading GCP credentials: {e}")

# --- Clients ---
def init_clients():
    global speech_client, elevenlabs_client
    load_gcp_credentials()
    speech_client = speech.SpeechClient()
    elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

init_clients()

# --- Bot Logic ---
def get_bot_response(text):
    logger.info(f"User: {text}")
    if "שלום" in text or "היי" in text:
        return "שלום גם לך! אני בוט קולי. מה שלומך?"
    elif "שם" in text:
        return "שמי בוט, ואני שמח לדבר איתך."
    return "לא הבנתי את מה שאמרת. אפשר לחזור על זה?"

# --- Voice Webhook ---
@app.route("/voice", methods=["POST"])
def voice():
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url="wss://web-production-770fa.up.railway.app/stream")
    response.append(connect)
    logger.info("Generated TwiML for call.")
    return str(response), 200, {"Content-Type": "application/xml"}

# --- WebSocket Route ---
@app.route("/stream")
def stream():
    logger.info("🔌 /stream endpoint was called")

    if request.environ.get("wsgi.websocket"):
        logger.info("✅ WebSocket upgrade successful")
        ws = request.environ["wsgi.websocket"]
        logger.info("WebSocket connected.")

        stream_sid = request.environ.get("HTTP_X_TWILIO_STREAM_SID", "unknown_sid")

        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
            sample_rate_hertz=SAMPLE_RATE,
            language_code=LANGUAGE_CODE,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=False,
            single_utterance=True,
        )

        def request_generator():
            while not ws.closed:
                message = ws.receive()
                if message is None:
                    break
                data = json.loads(message)
                if data.get("event") == "media":
                    audio = base64.b64decode(data["media"]["payload"])
                    yield speech.StreamingRecognizeRequest(audio_content=audio)

        try:
            responses = speech_client.streaming_recognize(streaming_config, request_generator())
            for response in responses:
                if not response.results or not response.results[0].alternatives:
                    continue

                result = response.results[0]
                if result.is_final:
                    transcript = result.alternatives[0].transcript
                    bot_response = get_bot_response(transcript)

                    logger.info(f"TTS: {bot_response}")
                    audio_stream = elevenlabs_client.generate(
                        text=bot_response,
                        voice=ELEVENLABS_VOICE_ID,
                        model="eleven_multilingual_v2",
                        stream=True,
                        output_format="pcm_16000"
                    )

                    # Notify Twilio
                    ws.send(json.dumps({
                        "event": "mark",
                        "streamSid": stream_sid,
                        "mark": {"name": "bot_response_start"}
                    }))

                    for chunk in audio_stream:
                        if chunk:
                            audio = AudioSegment(
                                data=chunk,
                                sample_width=2,
                                frame_rate=16000,
                                channels=1
                            ).set_frame_rate(SAMPLE_RATE)
                            mulaw = audio.export(format="mulaw").read()
                            encoded = base64.b64encode(mulaw).decode("utf-8")
                            ws.send(json.dumps({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": encoded}
                            }))
                    break

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            ws.close()
            logger.info("WebSocket closed.")

    else:
        logger.warning("❌ Non-WebSocket request to /stream")
        return "This endpoint is for WebSocket only", 400

# --- Main Entry ---
if __name__ == "__main__":
    logger.info("Starting app with gevent WSGIServer...")
    server = pywsgi.WSGIServer(
        ("0.0.0.0", int(os.environ.get("PORT", 8080))),
        app,
        handler_class=WebSocketHandler
    )
    server.serve_forever()
