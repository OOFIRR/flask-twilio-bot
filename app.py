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
import tempfile # ייבוא חדש עבור קבצים זמניים

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
    global _gcp_creds_file_path # משתנה גלובלי לשמירת הנתיב לקובץ הזמני
    _gcp_creds_file_path = None # אתחול

    try:
        if not GCP_CREDENTIALS_JSON:
            logger.critical("GCP_CREDENTIALS_JSON environment variable is not set. Cannot proceed with GCP authentication.")
            return False

        # יצירת קובץ זמני עם tempfile כדי לנהל אותו באופן בטוח יותר
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as temp_creds_file:
            temp_creds_file.write(GCP_CREDENTIALS_JSON)
            _gcp_creds_file_path = temp_creds_file.name
        
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _gcp_creds_file_path
        logger.info(f"GCP credentials written to temporary file: {_gcp_creds_file_path} and GOOGLE_APPLICATION_CREDENTIALS set.")
        logger.info(f"Verifying temporary file existence: {os.path.exists(_gcp_creds_file_path)}")
        return True
    except json.JSONDecodeError as e:
        logger.critical(f"Error decoding GCP_CREDENTIALS_JSON: {e}. Check its content for valid JSON.", exc_info=True)
        return False
    except Exception as e:
        logger.critical(f"Critical error during GCP credentials loading: {e}", exc_info=True)
        return False


# --- Clients ---
def init_clients():
    global speech_client, elevenlabs_client
    logger.info("Initializing Google Cloud Speech and ElevenLabs clients...")
    
    if not load_gcp_credentials():
        logger.critical("Failed to load GCP credentials. Speech-to-Text will not work.")
        speech_client = None # Set to None to indicate failure
    else:
        try:
            speech_client = speech.SpeechClient()
            logger.info("Google Cloud Speech client initialized successfully.")
        except Exception as e:
            logger.critical(f"Failed to initialize Google Cloud Speech client AFTER loading credentials: {e}", exc_info=True)
            speech_client = None

    if not ELEVENLABS_API_KEY:
        logger.critical("ELEVENLABS_API_KEY environment variable is not set. Text-to-Speech will not work.")
        elevenlabs_client = None # Set to None to indicate failure
    else:
        elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        logger.info("ElevenLabs client initialized.")

init_clients()

# --- Cleanup function for temporary GCP credentials file ---
def cleanup_gcp_creds_file():
    if '_gcp_creds_file_path' in globals() and _gcp_creds_file_path and os.path.exists(_gcp_creds_file_path):
        try:
            os.remove(_gcp_creds_file_path)
            logger.info(f"Temporary GCP credentials file '{_gcp_creds_file_path}' removed.")
        except Exception as e:
            logger.warning(f"Could not remove temporary GCP credentials file '{_gcp_creds_file_path}': {e}")

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
    stream_url = os.environ.get("WEBSOCKET_STREAM_URL", "wss://web-production-770fa.up.railway.app/stream")
    connect.stream(url=stream_url)
    response.append(connect)
    logger.info(f"Generated TwiML for call with stream URL: {stream_url}")
    return str(response), 200, {"Content-Type": "application/xml"}

# --- WebSocket Route ---
@app.route("/stream")
def stream():
    logger.info("🔌 /stream endpoint was called.")

    if request.environ.get("wsgi.websocket"):
        logger.info("✅ WebSocket upgrade successful. Attempting to connect.")
        ws = request.environ["wsgi.websocket"]
        logger.info("WebSocket connected.")

        stream_sid = request.environ.get("HTTP_X_TWILIO_STREAM_SID", "unknown_sid")
        logger.info(f"Stream SID: {stream_sid}")

        if speech_client is None:
            logger.error("Speech client is not initialized. Cannot perform speech recognition.")
            ws.close()
            return "Speech client not ready", 500

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
        logger.info("Google Speech Recognition config set.")

        def request_generator():
            logger.info("Starting WebSocket request generator...")
            while not ws.closed:
                try:
                    message = ws.receive()
                    if message is None:
                        logger.warning("Received None message from WebSocket (client disconnected?). Breaking generator.")
                        break
                    data = json.loads(message)
                    if data.get("event") == "media":
                        audio = base64.b64decode(data["media"]["payload"])
                        yield speech.StreamingRecognizeRequest(audio_content=audio)
                    elif data.get("event") == "start":
                        logger.info(f"Twilio 'start' event received: {data}")
                    elif data.get("event") == "stop":
                        logger.info(f"Twilio 'stop' event received: {data}")
                        break # Stop generator when Twilio signals stop
                except json.JSONDecodeError as e:
                    logger.error(f"JSON Decode Error in WebSocket message: {e}", exc_info=True)
                    break
                except Exception as e:
                    logger.error(f"Unexpected error in WebSocket request generator: {e}", exc_info=True)
                    break
            logger.info("WebSocket request generator finished.")

        try:
            logger.info("Calling speech_client.streaming_recognize...")
            responses = speech_client.streaming_recognize(streaming_config, request_generator())
            logger.info("Speech client streaming recognize started.")

            for response in responses:
                logger.info(f"Received speech recognition response: {response}")
                if not response.results or not response.results[0].alternatives:
                    logger.debug("No speech results or alternatives found in response.")
                    continue

                result = response.results[0]
                if result.is_final:
                    transcript = result.alternatives[0].transcript
                    logger.info(f"Final transcript received: '{transcript}'")
                    bot_response = get_bot_response(transcript)

                    if elevenlabs_client is None:
                        logger.error("ElevenLabs client is not initialized. Cannot perform Text-to-Speech.")
                        break

                    logger.info(f"Generating TTS for: '{bot_response}'")
                    audio_stream = elevenlabs_client.generate(
                        text=bot_response,
                        voice=ELEVENLABS_VOICE_ID,
                        model="eleven_multilingual_v2",
                        stream=True,
                        output_format="pcm_16000"
                    )
                    logger.info("ElevenLabs audio stream started.")

                    # Notify Twilio that bot response is starting
                    ws.send(json.dumps({
                        "event": "mark",
                        "streamSid": stream_sid,
                        "mark": {"name": "bot_response_start"}
                    }))
                    logger.info("Sent 'mark' event to Twilio.")

                    for chunk in audio_stream:
                        if chunk:
                            try:
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
                            except Exception as e:
                                logger.error(f"Error processing or sending audio chunk: {e}", exc_info=True)
                                break
                    logger.info("Finished sending bot audio chunks.")
                    break # Assuming single utterance interaction

        except Exception as e:
            logger.error(f"Critical WebSocket handler error: {e}", exc_info=True) # Stack trace here
        finally:
            logger.info("WebSocket connection closing...")
            if not ws.closed:
                ws.close()
            logger.info("WebSocket closed.")

    else:
        logger.warning("❌ Non-WebSocket request to /stream endpoint. This endpoint expects a WebSocket upgrade.")
        return "This endpoint is for WebSocket only", 400

# --- Main Entry ---
if __name__ == "__main__":
    logger.info("Starting app with gevent WSGIServer...")
    server = pywsgi.WSGIServer(
        ("0.0.0.0", int(os.environ.get("PORT", 8080))),
        app,
        handler_class=WebSocketHandler
    )
    logger.info(f"WSGIServer listening on 0.0.0.0:{os.environ.get('PORT', 8080)}")
    try:
        server.serve_forever()
    except Exception as e:
        logger.critical(f"Failed to start WSGIServer: {e}", exc_info=True)
    finally:
        cleanup_gcp_creds_file() # Ensure temporary file is cleaned up on exit