#!/bin/sh

# ודא ש-$PORT קיים, אחרת נשתמש ב-8080 כברירת מחדל
PORT=${PORT:-8080}

echo "Starting Gunicorn on port $PORT..."
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:$PORT app:app
