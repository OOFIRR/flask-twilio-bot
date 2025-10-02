import os
import base64
import json
import logging
from io import BytesIO

from flask import Flask, request
from flask_sockets import Sockets
from twilio.twiml.voice_response import VoiceResponse, Connect
from google.cloud import speech
from elevenlabs.client import ElevenLabs
from pydub import AudioSegment

# --- Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
GCP_CREDENTIALS_JSON = os.environ.get('GCP_CREDENTIALS_JSON')
ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY')
ELEVENLABS_VOICE_ID = os.environ.get('ELEVENLABS_VOICE_ID')

# Audio Settings
SAMPLE_RATE = 8000  # 8kHz for telephony
LANGUAGE_CODE = 'he-IL'

# --- Initialize App ---
app = Flask(__name__)
sockets = Sockets(app)

# --- Load Google Credentials ---
def load_gcp_credentials():
    if not GCP_CREDENTIALS_JSON:
        logger.error("GCP_CREDENTIALS_JSON not set.")
        return
    try:
        creds = json.loads(GCP_CREDENTIALS_JSON)
        with open("gcp_creds.json", "w") as f:
            json.dump(creds, f)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "gcp_creds.json"
        logger.info("GCP credentials loaded.")
    except Exception as e:
        logger.error(f"Failed to load GCP credentials: {e}")

# --- Initialize Clients ---
def initialize_clients():
    global speech_client, elevenlabs_client
    try:
        load_gcp_credentials()
        speech_client = speech.SpeechClient()
        logger.info("Google Speech client ready.")
    except Exception as e:
        speech_client = None
        logger.error(f"Google Speech init error: {e}")

    try:
        if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
            raise ValueError("Missing ElevenLabs credentials.")
        elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        logger.info("ElevenLabs client ready.")
    except Exception as e:
        elevenlabs_client = None
        logger.error(f"ElevenLabs init error: {e}")

initialize_clients()

# --- Bot Logic ---
def get_bot_response(text):
    logger.info(f"User: {text}")
    if "שלום" in text or "היי" in text:
        return "שלום גם לך! אני בוט קולי. מה שלומך?"
    elif "שם" in text:
        return "שמי בוט, ואני שמח לדבר איתך."
    return "לא הבנתי את מה שאמרת. אפשר לחזור על זה?"

# --- Voice Endpoint for Twilio ---
@app.route("/voice", methods=["POST"])
def voice():
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{request.host}/stream")
    response.append(connect)
    logger.info("TwiML generated. Connecting to WebSocket.")
    return str(response), 200, {"Content-Type": "application/xml"}

# --- WebSocket Stream Handler ---
@sockets.route("/stream")
def stream_socket(ws):
    if not speech_client or not elevenlabs_client:
        logger.error("Speech or ElevenLabs client not initialized.")
        return

    try:
        logger.info("WebSocket connection started.")
        stream_sid = request.environ.get("HTTP_X_TWILIO_STREAM_SID", "unknown_sid")

        # Google STT config
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

        def request_generator(ws_local):
            while not ws_local.closed:
                message = ws_local.receive()
                if message is None:
                    break
                try:
                    data = json.loads(message)
                    if data.get("event") == "media":
                        audio_data = base64.b64decode(data["media"]["payload"])
                        yield speech.StreamingRecognizeRequest(audio_content=audio_data)
                except Exception as e:
                    logger.warning(f"Error processing message: {e}")

        responses = speech_client.streaming_recognize(streaming_config, request_generator(ws))

        for response in responses:
            if not response.results or not response.results[0].alternatives:
                continue

            result = response.results[0]
            if result.is_final:
                transcript = result.alternatives[0].transcript
                bot_response_text = get_bot_response(transcript)

                logger.info(f"Generating TTS for: {bot_response_text}")
                audio_stream = elevenlabs_client.generate(
                    text=bot_response_text,
                    voice=ELEVENLABS_VOICE_ID,
                    model="eleven_multilingual_v2",
                    stream=True,
                    output_format="pcm_16000"
                )

                # Send "mark" to Twilio
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

                        mulaw_data = audio.export(format="mulaw").read()
                        encoded = base64.b64encode(mulaw_data).decode("utf-8")

                        ws.send(json.dumps({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": encoded}
                        }))
                break

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if not ws.closed:
            ws.close()
        logger.info("WebSocket closed.")
