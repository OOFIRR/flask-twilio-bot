
import os
import json
import logging
import asyncio
import base64

from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Connect
from google.cloud import speech
from elevenlabs.client import AsyncElevenLabs

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Environment Variables & Constants ---
# Ensure you have set these in your Railway environment variables
TWILIO_DOMAIN = os.environ.get("RAILWAY_STATIC_URL")
if not TWILIO_DOMAIN:
    raise ValueError("RAILWAY_STATIC_URL environment variable not set.")
VOICE_STREAM_URL = f"wss://{TWILIO_DOMAIN}/stream"

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID")
if not ELEVENLABS_API_KEY or not ELEVENLABS_VOICE_ID:
    raise ValueError("ELEVEN_API_KEY and ELEVEN_VOICE_ID environment variables must be set.")
# Use the async client for our async handler
elevenlabs_client = AsyncElevenLabs(api_key=ELEVENLABS_API_KEY)

# Google Cloud Speech-to-Text configuration
SAMPLE_RATE = 8000
LANGUAGE_CODE = 'he-IL'
speech_client = speech.SpeechClient()
recognition_config = speech.RecognitionConfig(
    encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
    sample_rate_hertz=SAMPLE_RATE,
    language_code=LANGUAGE_CODE,
    model="telephony",
    use_enhanced=True,
)
streaming_config = speech.StreamingRecognitionConfig(
    config=recognition_config,
    interim_results=False,
    single_utterance=True
)

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Core Bot Logic ---
def handle_text_response(text: str) -> str:
    """Generates a response based on user input."""
    logger.info(f"User said: '{text}'")
    text_lower = text.lower().strip()
    if "שלום" in text_lower or "היי" in text_lower:
        return "שלום גם לך! אני בוט שיחה. איך אני יכול לעזור?"
    elif "שם" in text_lower:
        return "השם שלי הוא בוט, ואני שמח לדבר איתך."
    else:
        return "לא כל כך הבנתי. אפשר לנסות שוב בבקשה?"

async def send_audio_response_to_twilio(ws, stream_sid: str, text_to_speak: str):
    """Generates audio with ElevenLabs and streams it back to Twilio."""
    logger.info(f"Generating audio for: '{text_to_speak}'")
    try:
        audio_stream = await elevenlabs_client.generate(
            text=text_to_speak,
            voice=ELEVENLABS_VOICE_ID,
            model="eleven_multilingual_v2",
            stream=True,
            output_format="mulaw_8000"
        )

        async for audio_chunk in audio_stream:
            if audio_chunk:
                payload = base64.b64encode(audio_chunk).decode("utf-8")
                media_message = {
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": payload}
                }
                await ws.send(json.dumps(media_message))

        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {"name": "end_of_bot_speech"}
        }
        await ws.send(json.dumps(mark_message))
        logger.info("Finished streaming audio response.")
    except Exception as e:
        logger.error(f"Error during TTS generation or streaming: {e}")

# --- WebSocket Handler ---
async def twilio_stream_handler(ws):
    logger.info("WebSocket connection established.")
    stream_sid = None

    async def audio_generator_from_twilio(ws_client):
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        while not ws_client.closed:
            try:
                message = await ws_client.recv()
                data = json.loads(message)
                if data["event"] == "start":
                    nonlocal stream_sid
                    stream_sid = data['start']['streamSid']
                    logger.info(f"Twilio Start event. Stream SID: {stream_sid}")
                elif data["event"] == "media":
                    yield speech.StreamingRecognizeRequest(
                        audio_content=base64.b64decode(data['media']['payload'])
                    )
                elif data["event"] == "stop":
                    logger.info("Twilio Stop event received.")
                    break
            except Exception:
                break

    try:
        requests = audio_generator_from_twilio(ws)
        responses = speech_client.streaming_recognize(requests=requests)

        for response in responses:
            if not response.results or not response.results[0].alternatives:
                continue
            
            result = response.results[0]
            if result.is_final:
                transcript = result.alternatives[0].transcript.strip()
                logger.info(f"STT Final Transcript: '{transcript}'")
                if transcript and stream_sid:
                    bot_response_text = handle_text_response(transcript)
                    await send_audio_response_to_twilio(ws, stream_sid, bot_response_text)
                    break 
    except Exception as e:
        logger.error(f"Error during STT processing: {e}")
    finally:
        logger.info("Closing WebSocket connection.")
        if not ws.closed:
            await ws.close()

# --- Flask Routes ---
@app.route("/voice", methods=['POST'])
def voice():
    response = VoiceResponse()
    connect = Connect()
    response.say("שלום, אני מחבר אותך.", language="he-IL")
    connect.stream(url=VOICE_STREAM_URL)
    response.append(connect)
    response.pause(length=60)
    return str(response), 200, {'Content-Type': 'application/xml'}

@app.route('/stream')
def stream():
    if 'wsgi.websocket' in request.environ:
        ws = request.environ['wsgi.websocket']
        try:
            asyncio.run(twilio_stream_handler(ws))
        except Exception as e:
            logger.error(f"Error in stream handler: {e}")
        return Response(status=200)
    else:
        return "WebSocket connection expected.", 400
