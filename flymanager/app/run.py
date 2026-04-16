# flymanager/app/run.py
from flymanager.app import create_app, socketio

app = create_app()


def main():
    socketio.run(
        app,
        debug=app.config["FLASK_DEBUG"],
        host=app.config["APP_HOST"],
        port=app.config["APP_PORT"],
    )


if __name__ == '__main__':
    main()