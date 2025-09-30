FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# הפוך את start.sh לאקזקיוטבילי
RUN chmod +x /app/start.sh

# זה קריטי: הפוך אותו ל-entrypoint
ENTRYPOINT ["/app/start.sh"]
