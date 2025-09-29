FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt

# הגדרה אקספליציטית של משתנה הסביבה PORT (Railway תחליף אותו בזמן אמת)
ENV PORT=8080

# כאן הבעיה נפתרת – המשתנה PORT מוגדר עבור shell
CMD gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:$PORT app:app
