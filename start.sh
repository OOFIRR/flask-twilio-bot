#!/bin/sh

# אם PORT לא מוגדר, נשתמש ב-8080 כברירת מחדל
PORT=${PORT:-8080}

echo "Starting Gunicorn on port $PORT..."
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:$PORT app:app
#!/bin/sh

# שימוש ב-PORT מהסביבה, או ברירת מחדל ל-8080
PORT=${PORT:-8080}

echo "Starting Gunicorn on port $PORT..."
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.
