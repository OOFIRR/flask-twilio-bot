import os
import json
import logging
import asyncio
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

# ייבוא ספריות של Google Cloud לזיהוי דיבור
# הערה: יש לוודא שהספרייה google-cloud-speech מותקנת
try:
    from google.cloud import speech
    # הגדרת ה-logger
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # בדיקה אם יש מפתח API במשתני הסביבה (לצורך Railway/Dev)
    # Twilio יעביר את ה-API Key באופן אוטומטי אם הוא מוגדר, אבל נשתמש בו לאתחול
    # ההנחה היא שהמשתמש הגדיר את משתנה הסביבה GOOGLE_APPLICATION_CREDENTIALS
    # או ש-Railway משתמש ב-JSON Creds.

    # אתחול Google Speech Client (הClient יכול להיות כבד, נאתחל אותו פעם אחת)
    # השימוש ב-try/except כאן נועד לוודא שהאפליקציה לא קורסת אם אין אימות
    speech_client = speech.SpeechClient()
    logger.info("Google Speech Client initialized successfully.")

except ImportError:
    # אם הספרייה לא מותקנת, ניתן להמשיך להריץ את השרת אבל זיהוי הדיבור לא יעבוד.
    speech_client = None
    logger.warning("Google Cloud Speech library not found. Speech-to-Text functionality will be disabled.")
except Exception as e:
    speech_client = None
    logger.error(f"Error initializing Google Speech Client: {e}")


# --- הגדרות קבועות ---
TWILIO_DOMAIN = "web-production-770fa.up.railway.app" # כתובת ה-Railway שלך
VOICE_STREAM_URL = f"wss://{TWILIO_DOMAIN}/stream"
PORT = os.environ.get('PORT', 8080) # שימוש בפורט של Railway (8080)

# --- הגדרות זיהוי דיבור ---
# הגדרות מומלצות לזיהוי דיבור רציף של Twilio Media Streams
SAMPLE_RATE = 8000
LANGUAGE_CODE = 'he-IL' # עברית

# הגדרת הקונפיגורציה של Google Speech
recognition_config = speech.RecognitionConfig(
    encoding=speech.RecognitionConfig.AudioEncoding.MULAW, # Twilio שולח בפורמט Mulaw
    sample_rate_hertz=SAMPLE_RATE,
    language_code=LANGUAGE_CODE
)

# הגדרת הקונפיגורציה של זיהוי דיבור רציף
streaming_config = speech.StreamingRecognitionConfig(
    config=recognition_config,
    interim_results=False, # אנחנו רוצים תוצאות סופיות בלבד
    single_utterance=True # עצירה לאחר משפט אחד
)

# --- הגדרת אפליקציית Flask ---
app = Flask(__name__)


# פונקציית לוגיקת הבוט (כאן נכנסת כל האינטליגנציה)
def handle_text_response(text):
    """
    מטפל בטקסט שזוהה ומחזיר את תגובת הבוט.
    כאן נוכל לשלב מודל LLM או לוגיקה קבועה.
    """
    logger.info(f"Received user text: '{text}'")
    
    # לוגיקה פשוטה לצורך בדיקה ראשונית
    if "שלום" in text or "היי" in text:
        return "שלום לך! אני בוט שיחה. איך אני יכול לעזור היום?"
    elif "שם" in text:
        return "שמי הוא ג'מיני ופיתחו אותי על ידי גוגל. מה אתה אוהב לעשות?"
    else:
        return "אני לא בטוח שהבנתי. אתה יכול לחזור על זה שוב, בבקשה?"

# --- נקודת קצה לשיחה נכנסת (Twilio Webhook) ---
@app.route("/voice", methods=['GET', 'POST'])
def voice():
    """
    נקודת הכניסה שמקבלת את השיחה מ-Twilio.
    מחזירה TwiML שמורה ל-Twilio להתחבר ל-WebSocket.
    """
    try:
        response = VoiceResponse()
        
        # 1. קודם כל, נאמר משהו למתקשר כדי להבטיח שהשיחה לא נגמרת מיד (פתרון לבעיית ניתוק מוקדם)
        response.say("שלום, אני בוט השיחה שלך. אני מעביר אותך לשידור חי.", voice='he-IL', language='he-IL')
        
        # 2. מחברים את השיחה ל-Media Stream שלנו
        connect = Connect()
        # Twilio צריך להשתמש ב-wss (WebSocket Secure)
        connect.stream(
            name='MyStream',
            url=VOICE_STREAM_URL
        )
        response.append(connect)
        
        # 3. מוסיפים הפסקה ארוכה כדי שהשיחה לא תסתיים לפני ש-Twilio יתחבר ל-WebSocket
        # נדרש כדי שהחיבור יספיק להתבצע
        response.pause(length=60) # הפסקה של 60 שניות כדי לתת זמן לבוט להגיב
        
        logger.info(f"TwiML response: {str(response)}")
        
        return Response(str(response), mimetype='application/xml')

    except Exception as e:
        logger.error(f"Error in /voice webhook: {e}")
        return "<Response><Say>התרחשה שגיאה בחיבור לשירות. להתראות.</Say></Response>", 500


# --- WebSocket Handler ---

async def twilio_stream_handler(ws):
    """
    מטפל בחיבור ה-WebSocket הנכנס מ-Twilio.
    קורא את ההודעות, שולח אותן ל-STT, ומגיב.
    """
    if not speech_client:
        logger.error("Speech client is not initialized. Cannot handle stream.")
        await ws.close()
        return

    logger.info(f"WebSocket connection established for Session SID: {request.headers.get('Twilio-Sid')}")

    # יצירת מחולל (Generator) שיספק את נתוני האודיו ל-Google STT API
    async def generate_requests(ws):
        """
        קורא הודעות מ-WebSocket ומחזיר רק את נתוני האודיו.
        """
        # שולח את הבקשה הראשונה (streaming_config) ל-Google STT API
        # זה חייב להיות הפריט הראשון במחולל (Generator)
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)

        # לולאה שמקבלת נתונים מה-WebSocket
        while True:
            try:
                message = await ws.receive()
                if message is None or isinstance(message, bytes):
                    # אם החיבור נסגר או נתקבל מידע לא צפוי
                    break

                data = json.loads(message)
                
                if data["event"] == "start":
                    logger.info(f"Twilio Start event received. Stream details: {data['start']}")
                    continue
                
                if data["event"] == "connected":
                    logger.info(f"Twilio Connected event received.")
                    continue
                
                if data["event"] == "media":
                    # זהו זרם האודיו הרציף. Twilio שולח Base64-Encoded Audio
                    audio_base64 = data["media"]["payload"]
                    
                    # פענוח Base64 לנתוני אודיו בינאריים
                    audio_bytes = base64.b64decode(audio_base64)
                    
                    # שליחת האודיו ל-Google STT
                    yield speech.StreamingRecognizeRequest(audio_content=audio_bytes)

                if data["event"] == "stop":
                    logger.info("Twilio Stop event received. Ending stream.")
                    break
                    
                if data["event"] == "mark":
                    # Mark הוא אירוע דו-כיווני, אנחנו יכולים לקבל אותו או לשלוח אותו
                    # נתעלם ממנו כרגע
                    continue
                    
            except json.JSONDecodeError:
                logger.error("Failed to decode JSON message from WebSocket.")
                break
            except Exception as e:
                logger.error(f"Error reading from WebSocket: {e}")
                break

    # הפעלת זיהוי הדיבור של Google
    try:
        # הקריאה ל-Google STT היא איטרטור (מחזירה תגובות באופן רציף)
        responses = speech_client.streaming_recognize(
            requests=generate_requests(ws)
        )
        
        full_transcript = ""
        
        # טיפול בתגובות מ-Google STT
        for response in responses:
            if not response.results:
                continue

            result = response.results[0]
            if not result.alternatives:
                continue

            transcript = result.alternatives[0].transcript
            
            # אם זו תוצאה סופית (לא ביניים)
            if result.is_final:
                full_transcript += transcript
                
                # טיפול בתגובה של הבוט
                bot_response_text = handle_text_response(full_transcript)
                logger.info(f"Bot response generated: '{bot_response_text}'")
                
                # שליחת תגובת הבוט בחזרה ל-Twilio
                await send_response_to_twilio(ws, bot_response_text)
                
                # אחרי שקיבלנו תגובה ושלחנו אותה, אנחנו יכולים להתחיל להקשיב שוב
                # במודל של Single Utterance, הזרם נסגר אוטומטית אחרי תגובה סופית.
                # לכן נסגור את ה-WS ונחכה לחיבור חדש אם השיחה ממשיכה.
                break 

    except Exception as e:
        logger.error(f"Google STT streaming error: {e}")
        # אם יש שגיאה ב-STT, אנחנו סוגרים את ה-WebSocket
        
    finally:
        await ws.close()
        logger.info("WebSocket connection closed.")

async def send_response_to_twilio(ws, text_to_speak):
    """
    שולח הודעת <Mark> ל-Twilio כדי שיוכל לנגן את התגובה.
    בשלב זה, כדי לשלוח אודיו (TTS) בחזרה ל-Twilio, זה דורש לוגיקה מורכבת של
    <Mark> ושידור אודיו בפורמט L16 8khz.
    
    לצורך בדיקה ראשונית, נשתמש ב-Message to Twilio כדי לדעת מתי לסיים את הדיבור.
    """
    # כיוון שאין לנו כרגע לוגיקת TTS מובנית בצד השרת,
    # בשלב זה, אנו נשלח רק הודעה פשוטה ללוג
    
    # כדי שהבוט ידבר, Twilio צריך לקבל הודעת <Mark> ולאחר מכן נתוני אודיו.
    # זה דורש שימוש ב-Text-to-Speech (TTS) ושליחת הנתונים כ-L16 8khz.
    
    # מכיוון שאין לנו TTS בצד השרת כרגע, אנחנו יכולים רק להדפיס את התגובה
    # וצריך לחזור ל-TwiML כדי שה-TTS של Twilio ידבר. 
    # לצורך דוגמה זו, אנחנו מניחים שמשתמש יטמיע TTS כאן.
    
    logger.warning(f"Response '{text_to_speak}' was generated but not sent as audio. Needs TTS implementation.")
    
    # לאחר סיום עיבוד הדיבור, אנחנו סוגרים את ה-WS. 
    # Twilio יחזור לטפל בשאר ה-TwiML (response.pause(length=60)) 
    # ויחכה לפקודה הבאה.


# --- נקודת קצה של WebSocket ---
# GeventWebSocket צריך את ה-Decorator הזה
@app.route('/stream')
def stream():
    """
    נקודת קצה שמקבלת את חיבור ה-WebSocket.
    """
    if request.environ.get('wsgi.websocket'):
        ws = request.environ['wsgi.websocket']
        # Flask-Gevent-WebSocket פועל סינכרונית, אבל אנחנו מריצים async handler בתוכו
        # נשתמש ב-asyncio.run כדי להריץ את ה-Handler שלנו בתוך תהליך Gevent
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        loop.run_until_complete(twilio_stream_handler(ws))
        
        return ""
    else:
        # זה קורה אם הגישה היא HTTP רגיל, לא WebSocket
        logger.warning("/stream accessed without WebSocket protocol.")
        return "Expected WebSocket connection", 400


# --- הפעלה מקומית / בדיקות ---
if __name__ == '__main__':
    # רק לצורך בדיקה מקומית. ב-Railway, ה-Procfile מפעיל את Gunicorn!
    import base64 # נדרש רק אם מריצים מחוץ ל-Gunicorn
    logger.info(f"Starting WSGI Server on port {PORT}...")
    # שימוש ב-WSGIServer של Gevent עם WebSocketHandler
    http_server = WSGIServer(('0.0.0.0', int(PORT)), app, handler_class=WebSocketHandler)
    http_server.serve_forever()

# ... (Your existing Flask app code) ...
# --- Add the following code to the VERY END of your app.py file ---

import os

if __name__ == "__main__":
    # Read the PORT environment variable provided by Railway
    port = int(os.environ.get("PORT", 5000)) # Default to 5000 for local testing
    
    # Run the app using Flask's built-in server
    # This is for debugging and will prove the PORT variable is readable
    print(f"INFO: Starting Flask app on host 0.0.0.0 and port {port}")
    app.run(host='0.0.0.0', port=port)
