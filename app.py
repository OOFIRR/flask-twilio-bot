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

# Twilio & Audio Settings
SAMPLE_RATE = 8000  # 8kHz for telephony
LANGUAGE_CODE = 'he-IL'

# --- Service Initialization ---
app = Flask(__name__)
sockets = Sockets(app)

def load_gcp_credentials():
    if not GCP_CREDENTIALS_JSON:
        logger.error("GCP_CREDENTIALS_JSON environment variable not set.")
        return None
    try:
        creds_json = json.loads(GCP_CREDENTIALS_JSON)
        with open("gcp_creds.json", "w") as f:
            json.dump(creds_json, f)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "gcp_creds.json"
        logger.info("GCP credentials loaded from environment variable.")
    except json.JSONDecodeError:
        logger.error("Failed to decode GCP_CREDENTIALS_JSON.")
        return None

def initialize_clients():
    global speech_client, elevenlabs_client
    try:
        load_gcp_credentials()
        speech_client = speech.SpeechClient()
        logger.info("Google Speech Client initialized successfully.")
    except Exception as e:
        speech_client = None
        logger.error(f"Error initializing Google Speech Client: {e}")

    try:
        if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
            raise ValueError("ElevenLabs API Key or Voice ID not set.")
        elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        logger.info("ElevenLabs Client initialized successfully.")
    except Exception as e:
        elevenlabs_client = None
        logger.error(f"Error initializing ElevenLabs Client: {e}")

initialize_clients()

# --- Bot Logic ---
def get_bot_response(text):
    logger.info(f"User said: '{text}'")
    if "שלום" in text or "היי" in text:
        return "שלום גם לך! אני בוט קולי. מה שלומך?"
    elif "שם" in text:
        return "שמי בוט, ואני שמח לדבר איתך."
    else:
        return "לא הבנתי את מה שאמרת. אפשר לחזור על זה?"

# --- Twilio Webhook for Incoming Calls ---
@app.route("/voice", methods=['POST'])
def voice():
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f'wss://{request.host}/stream')
    response.append(connect)
    logger.info("Generated TwiML for incoming call, connecting to WebSocket.")
    return str(response), 200, {'Content-Type': 'application/xml'}

# --- WebSocket Handler ---
@sockets.route('/stream')
def stream_socket(ws):
    if not speech_client or not elevenlabs_client:
        logger.error("A required client (Speech or ElevenLabs) is not initialized. Closing WebSocket.")
        return

    logger.info("WebSocket connection established.")
    stream_sid = request.environ.get('HTTP_X_TWILIO_STREAM_SID')

    # Google Speech-to-Text configuration
    recognition_config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
        sample_rate_hertz=SAMPLE_RATE,
        language_code=LANGUAGE_CODE
    )
    streaming_config = speech.StreamingRecognitionConfig(
        config=recognition_config,
        interim_results=False,
        single_utterance=True
    )

    def request_generator(ws_local):
        while not ws_local.closed:
            message = ws_local.receive()
            if message is None:
                break
            data = json.loads(message)
            if data['event'] == 'media':
                yield speech.StreamingRecognizeRequest(audio_content=base64.b64decode(data['media']['payload']))

    try:
        requests = request_generator(ws)
        responses = speech_client.streaming_recognize(streaming_config, requests)

        for response in responses:
            if not response.results or not response.results[0].alternatives:
                continue

            result = response.results[0]
            if result.is_final:
                transcript = result.alternatives[0].transcript
                bot_response_text = get_bot_response(transcript)

                # Generate audio with ElevenLabs
                logger.info(f"Generating audio for: '{bot_response_text}'")
                audio_stream = elevenlabs_client.generate(
                    text=bot_response_text,
                    voice=ELEVENLABS_VOICE_ID,
                    model="eleven_multilingual_v2",
                    stream=True,
                    output_format="pcm_16000" # PCM is easier to convert
                )

                # Stream audio back to Twilio
                if not ws.closed:
                    # Mark the start of our response
                    start_message = json.dumps({
                        "event": "mark",
                        "streamSid": stream_sid,
                        "mark": { "name": "bot_response_start" }
                    })
                    ws.send(start_message)

                    for chunk in audio_stream:
                        if chunk:
                            # Convert PCM 16kHz to Mulaw 8kHz
                            audio = AudioSegment(
                                data=chunk,
                                sample_width=2, # 16-bit PCM
                                frame_rate=16000,
                                channels=1
                            )
                            audio = audio.set_frame_rate(SAMPLE_RATE)
                            
                            # Export as mulaw
                            mulaw_data = audio.export(format="mulaw").read()

                            # Encode and send to Twilio
                            encoded_data = base64.b64encode(mulaw_data).decode('utf-8')
                            media_message = json.dumps({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": { "payload": encoded_data }
                            })
                            ws.send(media_message)
                
                # We handled one utterance, break to wait for next user input cycle
                break 

    except Exception as e:
        logger.error(f"Error during WebSocket stream: {e}")
    finally:
        if not ws.closed:
            ws.close()
        logger.info("WebSocket connection closed.")