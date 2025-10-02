import os
import subprocess
import sys

# Get the port number from the environment variable
port = os.environ.get('PORT')

# Check if the port variable exists. If not, exit with an error.
if port is None:
    print("Error: PORT environment variable not set.", file=sys.stderr)
    sys.exit(1)

# This is the command that will be executed.
# Python's f-string will correctly insert the port number.
command = [
    'gunicorn',
    '--worker-class',
    'geventwebsocket.gunicorn.workers.GeventWebSocketWorker',
    '--bind',
    f'0.0.0.0:{port}',
    'app:app'
]

print(f"Executing command: {' '.join(command)}")

# Execute the command
subprocess.run(command)