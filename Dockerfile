Use the official Python image with the slim tag for a smaller image size
FROM python:3.10-slim

Install system dependencies needed for ffpme (used in the original setup)
This section is kept as per your existing file, but only essential dependencies are installed
RUN apt-get update && 

apt-get install -y ffmpeg && 

rm -rf /var/lib/apt/lists/*

Set the working directory inside the container
WORKDIR /app

Copy the application code and requirements file
COPY . /app

Install Python dependencies from requirements.txt
--no-cache-dir speeds up the installation
RUN pip install --no-cache-dir -r requirements.txt

--- CRITICAL FIX SECTION ---
1. Ensure the start.sh script has execution permissions.
This solves the "connection refused" error when the container starts.
RUN chmod +x /app/start.sh

2. Define the command to run the application.
We use CMD to explicitly run start.sh via bash,
which handles the Gunicorn/Gevent setup and hardcoded port 8080.
CMD ["bash", "./start.sh"]