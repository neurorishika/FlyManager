# flymanager/app/__init__.py
"""
Flask application factory module.
Creates and configures the Flask application.
"""

import os
import re
import secrets
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, session, url_for
from flask_apscheduler import APScheduler
from flask_cors import CORS
from flask_mail import Mail
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix

from flask_session import Session as FlaskSession
# internal imports
from flymanager.app.security import (add_security_headers, csrf,
                                     generate_csp_nonce, limiter,
                                     parse_allowed_origins)
from flymanager.utils.mongo import (create_mongo_client, ensure_mongo_indexes,
                                    get_all_users, get_database,
                                    get_flip_schedule, get_settings,
                                    get_user_email, ping_database,
                                    preload_metadata_cache)
from flymanager.utils.stock_sources import preload_flybase_stock_indexes

# Load environment variables
load_dotenv()

# --- Global Variables / Constants ---
MIN_FLIP_DIFFERENCE = 12 * 60 * 60  # 12 hours
ALLOWED_EXTENSIONS = {"xlsx"}
active_threads = {}  # For QR Scanner

# --- Extension Instances ---
db = get_database(create_mongo_client())
ensure_mongo_indexes(db)
mail = Mail()
socketio = SocketIO(async_mode=os.getenv("SOCKETIO_ASYNC_MODE", "threading"))
scheduler = APScheduler()
sess = FlaskSession()
cors = CORS()


def env_flag(name, default=False):
    """Interpret common string environment values as booleans."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Helper Functions ---
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def abbreviate_species_name(value):
    """Render common Drosophila species names in a compact table-friendly form."""
    if value is None:
        return ""

    text = str(value).strip()
    if not text:
        return ""

    explicit_map = {
        "D. melanogaster": "D. mel",
        "D. simulans": "D. sim",
        "D. sechellia": "D. sec",
        "D. yakuba": "D. yak",
        "D. ananassae": "D. ana",
        "D. virilis": "D. vir",
        "D. erecta": "D. ere",
        "D. mauritiana": "D. mau",
        "D. pseudoobscura": "D. pse",
    }
    if text in explicit_map:
        return explicit_map[text]

    normalized = re.sub(r"\s+", " ", text)
    parts = normalized.split(" ")
    if len(parts) >= 2 and parts[0].lower() in {"d.", "drosophila"}:
        species = re.sub(r"[^a-zA-Z-]", "", parts[1]).lower()
        if species:
            return f"D. {species[:3]}"

    return text


# --- Application Factory ---
def create_app():
    """Create and configure the Flask application."""

    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder="templates",
        static_folder="static",
    )
    app.add_template_filter(abbreviate_species_name, "species_abbrev")

    # --- Configuration ---
    session_lifetime_seconds = int(os.getenv("SESSION_LIFETIME_SECONDS", "3600"))
    provided_secret_key = (os.getenv("SECRET_KEY") or "").strip()
    secure_cookie_enabled = env_flag("SESSION_COOKIE_SECURE", False)
    production_domain = (os.getenv("FLYMANAGER_DOMAIN") or "").strip()
    if not provided_secret_key and (secure_cookie_enabled or production_domain):
        raise RuntimeError(
            "SECRET_KEY must be explicitly set for HTTPS or public-domain deployments."
        )

    app.config["SECRET_KEY"] = provided_secret_key or secrets.token_hex(32)
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_PERMANENT"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        seconds=session_lifetime_seconds
    )
    app.config["SESSION_FILE_DIR"] = os.getenv(
        "SESSION_FILE_DIR", "/tmp/flymanager/flask_session"
    )
    app.config["SESSION_FILE_THRESHOLD"] = 500
    app.config["SESSION_FILE_MODE"] = 0o600  # Use octal literal
    app.config["SESSION_USE_SIGNER"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv(
        "SESSION_COOKIE_SAMESITE", "Lax"
    )
    app.config["SESSION_COOKIE_SECURE"] = secure_cookie_enabled
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "data/uploads")
    app.config["PHENOTYPE_IMAGE_LIBRARY_PATH"] = os.getenv(
        "FLYMANAGER_PHENOTYPE_IMAGE_LIBRARY_PATH",
        os.path.abspath(os.path.join(app.root_path, "..", "..", "data", "phenotype_images")),
    )
    app.config["MAX_CONTENT_LENGTH"] = int(
        os.getenv("MAX_UPLOAD_SIZE_BYTES", str(8 * 1024 * 1024))
    )
    app.config["APP_PORT"] = int(os.getenv("APP_PORT", "5234"))
    app.config["APP_HOST"] = os.getenv("APP_HOST", "0.0.0.0")
    app.config["FLASK_DEBUG"] = env_flag("FLASK_DEBUG", False)
    app.config["MAIL_SUPPRESS_SEND"] = env_flag("MAIL_SUPPRESS_SEND", False)
    app.config["WTF_CSRF_TIME_LIMIT"] = session_lifetime_seconds
    app.config["WTF_CSRF_CHECK_DEFAULT"] = True
    app.config["RATELIMIT_STORAGE_URI"] = os.getenv(
        "RATELIMIT_STORAGE_URI", "memory://"
    )
    app.config["ENABLE_CLIENT_SERIAL_SCANNER"] = env_flag(
        "ENABLE_CLIENT_SERIAL_SCANNER", False
    )
    app.config["ENABLE_CAMERA_SCANNER"] = env_flag(
        "ENABLE_CAMERA_SCANNER", True
    )

    # Ensure upload and session directories exist
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["SESSION_FILE_DIR"], exist_ok=True)

    if env_flag("TRUST_PROXY_HEADERS", bool(production_domain)):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

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
    csrf.init_app(app)
    limiter.init_app(app)

    cors_allowed_origins = parse_allowed_origins(os.getenv("CORS_ALLOWED_ORIGINS"))
    socketio.init_app(app, cors_allowed_origins=cors_allowed_origins)
    if cors_allowed_origins:
        cors.init_app(app, resources={r"/*": {"origins": cors_allowed_origins}})

    mail.init_app(app)
    scheduler.init_app(app)
    app.after_request(add_security_headers)

    if not provided_secret_key:
        app.logger.warning(
            "SECRET_KEY was not provided; using an ephemeral key for local-only runtime."
        )

    # --- Make settings available to all templates ---
    @app.context_processor
    def inject_settings():
        flybase_sync_indicator = None
        if session.get("username") == "admin":
            try:
                from flymanager.app.services import flybase as flybase_service

                flybase_sync_indicator = flybase_service.get_flybase_reference_status(app)
            except Exception as exc:
                app.logger.warning(
                    "Unable to load FlyBase sync indicator for template context: %s",
                    exc,
                )

        return dict(
            settings=get_settings(db),
            csp_nonce=generate_csp_nonce(),
            flybase_sync_indicator=flybase_sync_indicator,
        )

    @app.before_request
    def ensure_csp_nonce():
        generate_csp_nonce()

    with app.app_context():
        # --- Import and Register Blueprints ---
        from flymanager.app.routes import (auth, cross, data, flip, main,
                                           settings, stock, tray)

        app.register_blueprint(main.bp)
        app.register_blueprint(auth.bp)
        app.register_blueprint(stock.bp, url_prefix="/stock")
        app.register_blueprint(cross.bp, url_prefix="/cross")
        app.register_blueprint(flip.bp, url_prefix="/flip")
        app.register_blueprint(data.bp, url_prefix="/data")
        app.register_blueprint(tray.bp, url_prefix="/tray")
        app.register_blueprint(settings.bp)

        if env_flag("WARM_PAGE_CACHES_ON_STARTUP", True):
            try:
                preload_metadata_cache(db)
            except Exception as exc:
                app.logger.warning(
                    "Unable to preload metadata cache during startup: %s",
                    exc,
                )

            try:
                preload_flybase_stock_indexes()
            except Exception as exc:
                app.logger.warning(
                    "Unable to preload FlyBase stock indexes during startup: %s",
                    exc,
                )

        # --- Import Services (to ensure they are loaded) ---
        from flymanager.app.services import bloomington as bloomington_service
        from flymanager.app.services import email as email_service
        from flymanager.app.services import flybase as flybase_service
        from flymanager.app.services import scanner as scanner_service
        from flymanager.app.services import scheduler as scheduler_service

        # --- Initialize Scheduler ---
        if env_flag("ENABLE_SCHEDULER", True) and not scheduler.running:
            # Add scheduled job using the function from services
            scheduler.add_job(
                id="daily_flip_reminder_job",
                func=scheduler_service.schedule_daily_flip_reminders,
                trigger="cron",
                hour=8,
                minute=0,
                args=[app],  # Pass the app instance to the scheduled function
                replace_existing=True,
            )
            # Add monthly FlyBase reference refresh job (1st day of each month at 1:30 AM)
            scheduler.add_job(
                id="monthly_flybase_reference_refresh_job",
                func=flybase_service.update_flybase_reference_data,
                trigger="cron",
                day=1,
                hour=1,
                minute=30,
                args=[app],
                replace_existing=True,
            )
            # Add monthly legacy Bloomington compatibility refresh job (1st day of each month at 2 AM)
            scheduler.add_job(
                id="monthly_bloomington_update_job",
                func=bloomington_service.update_bloomington_stock_data,
                trigger="cron",
                day=1,
                hour=2,
                minute=0,
                args=[app],
                replace_existing=True,
            )
            scheduler.start()
            app.logger.info("Scheduler started.")

        @app.get("/health")
        def healthcheck():
            return jsonify({"status": "ok"}), 200

        @app.get("/health/ready")
        def readiness_check():
            if ping_database(db):
                return jsonify({"status": "ready", "mongo": "ok"}), 200
            return jsonify({"status": "not_ready", "mongo": "unavailable"}), 503

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
