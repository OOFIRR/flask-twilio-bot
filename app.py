import os
import json
import logging
import asyncio
import base64
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

# --- הגדרת Logger ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- אתחול שירותי Google Cloud ---
try:
    from google.cloud import speech
    speech_client = speech.SpeechClient()
    logger.info("Google Speech Client initialized successfully.")
except ImportError:
    speech_client = None
    logger.warning("Google Cloud Speech library not found. Speech-to-Text functionality will be disabled.")
except Exception as e:
    speech_client = None
    logger.error(f"Error initializing Google Speech Client: {e}. Check credentials.")

# --- הגדרות קבועות ותצורה ---
# קריאת משתני סביבה. חשוב להגדיר אותם ב-Railway.
RAILWAY_PUBLIC_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN', 'localhost')
PORT = int(os.environ.get('PORT', 8080))
VOICE_STREAM_URL = f"wss://{RAILWAY_PUBLIC_DOMAIN}/stream"

# הגדרות זיהוי דיבור (STT)
SAMPLE_RATE = 8000
LANGUAGE_CODE = 'he-IL'  # עברית

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

# --- אתחול אפליקציית Flask ---
app = Flask(__name__)

# --- פונקציות ליבה של הבוט ---

def handle_text_response(text):
    """
    מטפל בטקסט שזוהה ומחזיר את תגובת הבוט.
    TODO: להחליף את הלוגיקה הפשוטה בקריאה למודל LLM (כמו OpenAI).
    """
    logger.info(f"Received user text: '{text}'")
    if "שלום" in text or "היי" in text:
        return "שלום לך! אני בוט שיחה. איך אני יכול לעזור היום?"
    elif "שם" in text:
        return "שמי הוא ג'מיני ופותחתי על ידי גוגל. מה שלומך?"
    else:
        return "לא הבנתי. אפשר לחזור על זה שוב, בבקשה?"

async def generate_and_send_tts_audio(ws, text_to_speak):
    """
    ממיר טקסט לדיבור (TTS) ושולח את האודיו בחזרה ל-Twilio.
    TODO: לממש את הלוגיקה המלאה כאן:
    1. קריאה ל-API של ElevenLabs עם הטקסט.
    2. קבלת קובץ האודיו (MP3 או אחר).
    3. המרת האודיו לפורמט הנדרש על ידי Twilio (mulaw, 8000hz).
    4. שליחת הודעת 'mark' ל-Twilio כדי לסמן את תחילת התגובה.
    5. שליחת נתוני האודיו הבינאריים דרך ה-WebSocket.
    """
    logger.warning(f"TTS Response '{text_to_speak}' was generated but not sent as audio. TTS needs implementation.")
    # כאן תתווסף הלוגיקה של ElevenLabs
    pass

# --- נקודות קצה (Endpoints) ---

@app.route("/voice", methods=['POST'])
def voice_webhook():
    """
    נקודת הכניסה שמקבלת את השיחה מ-Twilio.
    מחזירה TwiML שמורה ל-Twilio להתחבר ל-WebSocket.
    """
    try:
        response = VoiceResponse()
        response.say("שלום, אני בוט השיחה שלך. אני מאזין.", voice='he-IL', language='he-IL')
        connect = Connect()
        connect.stream(name='MyStream', url=VOICE_STREAM_URL)
        response.append(connect)
        response.pause(length=60)
        logger.info(f"Generated TwiML for incoming call: {str(response)}")
        return Response(str(response), mimetype='application/xml')
    except Exception as e:
        logger.error(f"Error in /voice webhook: {e}")
        return Response("<Response><Say>התרחשה שגיאה.</Say></Response>", mimetype='application/xml', status=500)


@app.route('/stream')
def stream_websocket():
    """
    נקודת קצה המטפלת בחיבור ה-WebSocket מ-Twilio.
    """
    ws = request.environ.get('wsgi.websocket')
    if not ws:
        logger.error("/stream endpoint called without WebSocket context.")
        return "Expected WebSocket connection", 400

    # שימוש ב-asyncio להרצת הלוגיקה הא-סינכרונית בתוך סביבת Gevent
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(handle_twilio_stream(ws))
    finally:
        loop.close()
    return ""


async def handle_twilio_stream(ws):
    """
    הלוגיקה המרכזית המטפלת בזרם האודיו הנכנס.
    """
    if not speech_client:
        logger.error("Speech client not initialized. Closing WebSocket.")
        return

    logger.info("WebSocket connection established.")

    async def audio_generator(ws):
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        while not ws.closed:
            try:
                message = ws.receive()
                if message is None:
                    break
                data = json.loads(message)
                if data["event"] == "media":
                    yield speech.StreamingRecognizeRequest(audio_content=base64.b64decode(data["media"]["payload"]))
                elif data["event"] == "stop":
                    logger.info("Twilio 'stop' event received.")
                    break
            except Exception as e:
                logger.error(f"Error during WebSocket receive/processing: {e}")
                break

    try:
        responses = speech_client.streaming_recognize(requests=audio_generator(ws))
        for response in responses:
            if response.results and response.results[0].alternatives:
                transcript = response.results[0].alternatives[0].transcript
                if response.results[0].is_final:
                    bot_response_text = handle_text_response(transcript)
                    await generate_and_send_tts_audio(ws, bot_response_text)
                    break
    except Exception as e:
        logger.error(f"Google STT streaming recognition error: {e}")
    finally:
        if not ws.closed:
            ws.close()
        logger.info("WebSocket connection closed.")


# --- הרצה מקומית לצורכי פיתוח ---
if __name__ == '__main__':
    logger.info(f"Starting development server on http://0.0.0.0:{PORT}")
    http_server = WSGIServer(('0.0.0.0', PORT), app, handler_class=WebSocketHandler)
    http_server.serve_forever()