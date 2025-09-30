#!/bin/bash

# Log the port Railway is assigning for debugging
echo "INFO: Attempting to bind to 0.0.0.0:$PORT"

# Run the Gunicorn server
# --bind 0.0.0.0:$PORT is crucial for Railway deployments.
# It tells Gunicorn to listen on the port specified by the $PORT environment variable.
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --bind 0.0.0.0:$PORT app:app