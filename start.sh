#!/bin/bash
# Ensures the script exits if any command fails.
set -e

# Log the port for debugging and use double quotes for variable expansion.
echo "INFO: Attempting to bind to 0.0.0.0:$PORT"

# The --bind flag correctly uses the $PORT variable from the environment.
# This is required for deployments on Railway.
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind "0.0.0.0:$PORT" app:app