import os
import json
import logging
import base64
from io import BytesIO

from flask import Flask, request
from twilio.twiml.voice_response import VoiceResponse, Connect
from google.cloud import speech
from elevenlabs import ElevenLabs, Voice, VoiceSettings
from pydub import AudioSegment

# --- 1. Basic Configuration & Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 2. Environment Variable & API Client Initialization ---
try:
    # GCP Credentials Setup (from JSON string in env var)
    gcp_creds_json_str = os.environ["GCP_CREDENTIALS_JSON"]
    creds_path = "/tmp/gcp_creds.json"
    with open(creds_path, "w") as f:
        f.write(gcp_creds_json_str)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path
    speech_client = speech.SpeechClient()
    logger.info("Google Speech Client initialized successfully.")

    # ElevenLabs Client Setup
    elevenlabs_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])
    ELEVENLABS_VOICE_ID = os.environ["ELEVENLABS_VOICE_ID"]
    logger.info("ElevenLabs Client initialized successfully.")

except KeyError as e:
    logger.error(f"FATAL: Missing required environment variable: {e}. The application will not work.")
    speech_client = None
    elevenlabs_client = None
except Exception as e:
    logger.error(f"FATAL: Error during API client initialization: {e}. The application will not work.")
    speech_client = None
    elevenlabs_client = None

# --- 3. Constants and App Setup ---
TWILIO_WEBSOCKET_URL = f"wss://{os.environ.get('RAILWAY_STATIC_URL', 'your-domain.up.railway.app')}/stream"
SAMPLE_RATE = 8000
LANGUAGE_CODE = 'he-IL'

app = Flask(__name__)

# --- 4. Core Bot Logic ---

def simple_bot_logic(text: str) -> str:
    """A simple hardcoded logic for the bot's responses."""
    logger.info(f"User said: '{text}'")
    text_lower = text.lower()
    if "שלום" in text_lower or "היי" in text_lower:
        return "שלום גם לך! אני הבוט הקולי של Railway. מה שלומך?"
    elif "שם" in text:
        return "השם שלי הוא בוט-רכבת. נעים מאוד להכיר."
    elif "תודה" in text_lower:
        return "בבקשה! שמחתי לעזור."
    else:
        return "לא כל כך הבנתי. אפשר לנסות שוב?"

def generate_and_stream_tts(ws, text_to_speak: str, stream_sid: str):
    """Generates audio with ElevenLabs and streams it back to Twilio."""
    logger.info(f"Generating audio for: '{text_to_speak}'")
    try:
        # Generate audio using a blocking call, suitable for gevent
        response = elevenlabs_client.text_to_speech.convert(
            voice_id=ELEVENLABS_VOICE_ID,
            text=text_to_speak,
            model_id="eleven_multilingual_v2",
            voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75)
        )

        mp3_data = BytesIO()
        for chunk in response:
            mp3_data.write(chunk)
        mp3_data.seek(0)

        # Convert MP3 to the format Twilio requires (8000Hz mono µ-law)
        audio = AudioSegment.from_mp3(mp3_data)
        audio = audio.set_frame_rate(SAMPLE_RATE).set_channels(1)
        
        # Twilio Media Streams expect µ-law encoded audio data.
        # pydub's .raw_data gives us the raw PCM data, which we then need to encode.
        # For µ-law, pydub handles this internally when exporting.
        output_buffer = BytesIO()
        audio.export(output_buffer, format="mulaw")
        mulaw_data = output_buffer.getvalue()
        
        encoded_data = base64.b64encode(mulaw_data).decode('utf-8')
        
        # Send the audio back to Twilio as a media message
        media_message = {
            "event": "media",
            "streamSid": stream_sid,
            "media": {
                "payload": encoded_data
            }
        }
        ws.send(json.dumps(media_message))
        logger.info("Sent audio media to Twilio.")
        
        # Send a mark message to signal that we are done speaking
        mark_message = {
            "event": "mark",
            "streamSid": stream_sid,
            "mark": {
                "name": "bot_response_finished"
            }
        }
        ws.send(json.dumps(mark_message))
        logger.info("Sent 'mark' message to Twilio, signaling end of bot's turn.")

    except Exception as e:
        logger.error(f"Error during TTS generation or streaming: {e}")

# --- 5. Flask Routes ---

@app.route("/voice", methods=['POST'])
def voice_webhook():
    """Handles incoming calls from Twilio and connects them to the websocket."""
    response = VoiceResponse()
    connect = Connect()
    response.say("שלום, אני מאזין.", voice="Polly.Aditi", language="he-IL")
    connect.stream(url=TWILIO_WEBSOCKET_URL)
    response.append(connect)
    response.pause(length=120)
    logger.info("Generated TwiML for incoming call.")
    return str(response), 200, {'Content-Type': 'application/xml'}

@app.route('/stream')
def stream_websocket():
    """Handles the bidirectional websocket communication using gevent-websocket."""
    ws = request.environ.get('wsgi.websocket')
    if not ws:
        logger.error("Expected a websocket connection but none was found.")
        return "Expected a websocket connection", 400

    if not speech_client or not elevenlabs_client:
        logger.error("API clients not initialized. Closing websocket.")
        if not ws.closed:
             ws.close()
        return ""

    stream_sid = None
    
    def request_generator(ws_local):
        streaming_config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.MULAW,
                sample_rate_hertz=SAMPLE_RATE,
                language_code=LANGUAGE_CODE,
            ),
            interim_results=False,
            single_utterance=True,
        )
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)

        while not ws_local.closed:
            message = ws_local.receive()
            if message is None: break
            data = json.loads(message)
            if data['event'] == 'start':
                nonlocal stream_sid
                stream_sid = data['start']['streamSid']
                logger.info(f"Stream started: {stream_sid}")
            elif data['event'] == 'media':
                yield speech.StreamingRecognizeRequest(audio_content=base64.b64decode(data['media']['payload']))
            elif data['event'] == 'stop':
                logger.info("Stream stopped by Twilio.")
                break

    try:
        responses = speech_client.streaming_recognize(requests=request_generator(ws))
        for response in responses:
            if not response.results or not response.results[0].alternatives: continue
            result = response.results[0]
            if result.is_final:
                transcript = result.alternatives[0].transcript
                bot_response_text = simple_bot_logic(transcript)
                if ws and not ws.closed and stream_sid:
                    generate_and_stream_tts(ws, bot_response_text, stream_sid)
                break 
    except Exception as e:
        logger.error(f"Error during STT processing: {e}")
    finally:
        if not ws.closed:
            logger.info("Closing websocket from server.")
            ws.close()
    
    return ""
