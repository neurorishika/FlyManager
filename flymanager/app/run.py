# flymanager/app/run.py
from flymanager.app import create_app, socketio

app = create_app()

if __name__ == '__main__':
    # Use socketio.run to support WebSockets
    socketio.run(app, debug=True, host='0.0.0.0', port=5234) # Adjust host/port as needed