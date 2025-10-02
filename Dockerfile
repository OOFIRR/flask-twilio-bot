FROM python:3.10-slim

# Install system dependencies needed for FFmpeg
# This is CRITICAL for pydub to perform audio conversion (e.g., MP3 to µ-law)
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
