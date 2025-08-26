# flymanager/app/__init__.py
"""
Flask application factory module.
Creates and configures the Flask application.
"""

import os
import hashlib
import threading
from datetime import datetime

from flask import (
    Flask,
    session,
    redirect,
    jsonify,
    url_for,
    render_template,
    request,
    flash,
)
from flask_session import Session as FlaskSession
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_mail import Mail, Message
from flask_apscheduler import APScheduler
from dotenv import load_dotenv


# internal imports
from flymanager.utils.mongo import (
    create_mongo_client,
    get_database,
    get_all_users,
    get_user_email,
    get_flip_schedule,
    get_settings,
)
from flymanager.utils.scanner import get_available_ports  # Assuming this exists

# Load environment variables
load_dotenv()

# --- Global Variables / Constants ---
MIN_FLIP_DIFFERENCE = 12 * 60 * 60  # 12 hours
ALLOWED_EXTENSIONS = {"xlsx"}
active_threads = {}  # For QR Scanner

# --- Extension Instances ---
db = get_database(create_mongo_client())
mail = Mail()
socketio = SocketIO()
scheduler = APScheduler()
sess = FlaskSession()
cors = CORS()


# --- Helper Functions ---
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# --- Application Factory ---
def create_app():
    """Create and configure the Flask application."""

    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder="templates",
        static_folder="static",
    )

    # --- Configuration ---
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_PERMANENT"] = False
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600
    app.config["SESSION_FILE_DIR"] = (
        "/tmp/flask_session"  # Ensure this dir exists and is writable
    )
    app.config["SESSION_FILE_THRESHOLD"] = 500
    app.config["SESSION_FILE_MODE"] = 0o600  # Use octal literal
    app.config["UPLOAD_FOLDER"] = os.getenv(
        "UPLOAD_FOLDER", "/tmp/uploads"
    )  # Provide default

    # Ensure upload and session directories exist
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)

    app.config.update(
        MAIL_SERVER=os.getenv("SMTP_SERVER"),
        MAIL_PORT=int(os.getenv("SMTP_PORT", 587)),  # Provide default
        MAIL_USE_TLS=True,
        MAIL_USE_SSL=False,
        MAIL_USERNAME=os.getenv("SMTP_USERNAME"),
        MAIL_PASSWORD=os.getenv("SMTP_PASSWORD"),
        MAIL_DEFAULT_SENDER=os.getenv("SMTP_SENDER"),
    )

    sess.init_app(app)
    socketio.init_app(app)
    cors.init_app(app)
    mail.init_app(app)
    scheduler.init_app(app)

    # --- Make settings available to all templates ---
    @app.context_processor
    def inject_settings():
        return dict(settings=get_settings(db))

    with app.app_context():
        # --- Import and Register Blueprints ---
        from .routes import main, auth, stock, cross, flip, data, tray, settings

        app.register_blueprint(main.bp)
        app.register_blueprint(auth.bp)
        app.register_blueprint(stock.bp, url_prefix="/stock")
        app.register_blueprint(cross.bp, url_prefix="/cross")
        app.register_blueprint(flip.bp, url_prefix="/flip")
        app.register_blueprint(data.bp, url_prefix="/data")
        app.register_blueprint(tray.bp, url_prefix="/tray")
        app.register_blueprint(settings.bp)

        # --- Import Services (to ensure they are loaded) ---
        from .services import email as email_service
        from .services import scheduler as scheduler_service
        from .services import scanner as scanner_service

        # --- Initialize Scheduler ---
        if not scheduler.running:
            # Add scheduled job using the function from services
            scheduler.add_job(
                id="daily_flip_reminder_job",
                func=scheduler_service.schedule_daily_flip_reminders,
                trigger="cron",
                hour=8,
                minute=0,
                args=[app],  # Pass the app instance to the scheduled function
            )
            scheduler.start()
            print("Scheduler started.")

        @app.errorhandler(404)
        def page_not_found(error):
            return render_template("utilities/404.html", page_title="Not Found"), 404

        # --- Root Redirect ---
        @app.route("/")
        def index():
            if not session.get("username"):
                return redirect(url_for("auth.login"))
            return redirect(url_for("main.home"))

    return app


# --- SocketIO Events (if any global ones needed) ---
# Example:
# @socketio.on('connect')
# def handle_connect():
#     print('Client connected')
