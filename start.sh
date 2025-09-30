#!/bin/sh

# ברירת מחדל אם Railway לא הגדיר PORT
PORT=${PORT:-8080}

echo "Starting Gunicorn on port $PORT..."
exec gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:$PORT app:app
