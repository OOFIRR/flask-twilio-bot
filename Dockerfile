# ---- 1. Image בסיסי ----
FROM python:3.10-slim

# ---- 2. התקנת תלות מערכת נדרשת לעיבוד אודיו ----
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ---- 3. הגדרת תיקיית עבודה ----
WORKDIR /app

# ---- 4. העתקת כל הקבצים לקונטיינר ----
COPY . /app

# ---- 5. התקנת כל התלויות מה-requirements ----
RUN pip install --no-cache-dir -r requirements.txt

# ---- 6. הפעלת השרת עם Gunicorn + Gevent-WebSocket ----
# שימוש ב /bin/sh -c כדי שהמשתנה $PORT יורחב כמו שצריך בזמן ריצה
CMD /bin/sh -c "gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:${PORT} app:app"
