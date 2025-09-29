FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

# ודא ש-start.sh קיים ובעל הרשאות הרצה
RUN chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
