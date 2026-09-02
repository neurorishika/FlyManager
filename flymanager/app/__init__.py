# flymanager/app/__init__.py
"""
Flask application factory module.
Creates and configures the Flask application.
"""

import functools
import os
import re
import secrets
import time
from datetime import timedelta

from dotenv import load_dotenv
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   g, session, url_for)
from flask_apscheduler import APScheduler
from flask_cors import CORS
from flask_mail import Mail
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix

from flask_session import Session as FlaskSession
from redis import Redis
# internal imports
from flymanager.app.security import (add_security_headers, csrf,
                                     generate_csp_nonce, limiter,
                                     parse_allowed_origins)
from flymanager.utils.mongo import (OperationLockConflict, create_mongo_client,
                                    ensure_mongo_indexes, get_all_users,
                                    get_database, get_flip_schedule,
                                    get_settings, get_user_email,
                                    hold_operation_lock, ping_database,
                                    preload_metadata_cache)
from flymanager.utils.stock_sources import (preload_flybase_chromosome_lookup_tables,
                                             preload_flybase_stock_indexes)

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


def run_locked_scheduled_job(app, *, key, label, ttl_seconds, func):
    """Run a scheduled job under a Mongo-backed distributed lock.

    Guards APScheduler cron jobs against double-firing if the app is ever
    scaled to more than one replica. A lock conflict is a routine, expected
    skip (another replica already grabbed this tick), logged and swallowed
    rather than raised, so APScheduler never records it as a job failure.

    `key` should be the same lock/job key used by any admin "run now" route
    for the same underlying task (e.g. "maintenance:flybase-reference-refresh"),
    so a scheduled run and a manually-triggered run of the same work can
    never overlap.
    """
    with app.app_context():
        try:
            with hold_operation_lock(
                db,
                key=key,
                actor="scheduler",
                label=label,
                ttl_seconds=ttl_seconds,
                conflict_message=f"{label} is already running on another instance; skipping.",
            ):
                func(app)
        except OperationLockConflict as exc:
            app.logger.info("Skipped scheduled job %s: %s", key, exc)


# --- Application Factory ---
# The literal in .env.example. Anything that boots with this is running on a
# secret anyone can read out of the repository.
PLACEHOLDER_SECRET_KEY = "replace-with-a-long-random-string"


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
    if provided_secret_key == PLACEHOLDER_SECRET_KEY:
        # .env.example ships this value and scripts/install-production.sh used
        # to copy it verbatim, so a documented production install ran with a
        # secret published in this repository -- every session cookie, admin
        # included, was forgeable. Treated as no key at all.
        provided_secret_key = ""
    secure_cookie_enabled = env_flag("SESSION_COOKIE_SECURE", False)
    production_domain = (os.getenv("FLYMANAGER_DOMAIN") or "").strip()
    if not provided_secret_key and (secure_cookie_enabled or production_domain):
        raise RuntimeError(
            "SECRET_KEY must be explicitly set for HTTPS or public-domain deployments."
        )

    app.config["SECRET_KEY"] = provided_secret_key or secrets.token_hex(32)
    session_type = os.getenv("SESSION_TYPE", "filesystem").strip().lower()
    app.config["SESSION_TYPE"] = session_type
    app.config["SESSION_PERMANENT"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
        seconds=session_lifetime_seconds
    )
    app.config["SESSION_FILE_DIR"] = os.getenv(
        "SESSION_FILE_DIR", "/tmp/flymanager/flask_session"
    )
    app.config["SESSION_FILE_THRESHOLD"] = 500
    app.config["SESSION_FILE_MODE"] = 0o600  # Use octal literal
    if session_type == "redis":
        app.config["SESSION_REDIS"] = Redis.from_url(
            os.getenv("SESSION_REDIS_URL", "redis://redis:6379/1")
        )
    app.config["SESSION_REFRESH_EACH_REQUEST"] = env_flag(
        "SESSION_REFRESH_EACH_REQUEST", True
    )
    app.config["SESSION_USE_SIGNER"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = os.getenv(
        "SESSION_COOKIE_SAMESITE", "Lax"
    )
    app.config["SESSION_COOKIE_SECURE"] = secure_cookie_enabled
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "data/uploads")
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
    app_version = (os.getenv("APP_VERSION") or "dev").strip()
    app.config["APP_VERSION"] = app_version
    app.config["SLOW_REQUEST_THRESHOLD_MS"] = int(
        os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500")
    )
    # Cache policy is applied after the static response is built so only URLs
    # carrying the matching release token become immutable. This keeps direct
    # or CSS-relative unversioned asset URLs safe across deployments.
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # Ensure upload and session directories exist
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    if session_type == "filesystem":
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

    @app.url_defaults
    def add_static_asset_version(endpoint, values):
        if endpoint == "static":
            values.setdefault("v", app.config["APP_VERSION"])

    @app.before_request
    def start_request_timer():
        g.request_started_at = time.perf_counter()

    @app.after_request
    def add_performance_headers(response):
        started_at = getattr(g, "request_started_at", None)
        if started_at is not None:
            duration_ms = (time.perf_counter() - started_at) * 1000
            response.headers["Server-Timing"] = f"app;dur={duration_ms:.1f}"
            slow_threshold_ms = app.config["SLOW_REQUEST_THRESHOLD_MS"]
            if duration_ms >= slow_threshold_ms:
                app.logger.warning(
                    "Slow request method=%s path=%s status=%s duration_ms=%.1f",
                    request.method,
                    request.path,
                    response.status_code,
                    duration_ms,
                )
        if request.endpoint == "static" and app.config["APP_VERSION"] != "dev":
            response.cache_control.no_cache = None
            response.cache_control.public = True
            if request.args.get("v") == app.config["APP_VERSION"]:
                response.cache_control.max_age = 31536000
                response.cache_control.immutable = True
            else:
                response.cache_control.max_age = 3600
        return response

    if not provided_secret_key:
        app.logger.warning(
            "SECRET_KEY was not provided; using an ephemeral key for local-only runtime."
        )

    # --- Make settings available to all templates ---
    @app.context_processor
    def inject_settings():
        flybase_sync_indicator = None
        username = session.get("username")
        if username:
            if username == "admin":
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

    @app.before_request
    def refresh_marker_catalog_if_stale():
        """Converge this worker's marker catalog with Mongo, at most once per
        interval. A failure here must never fail the request: the previous
        snapshot stays installed and is at most one interval stale."""
        from flymanager.utils.phenotypes.marker_catalog import \
            maybe_refresh_catalog

        try:
            maybe_refresh_catalog(db)
        except Exception as exc:
            app.logger.warning("Unable to refresh the marker catalog: %s", exc)
        try:
            from flymanager.utils.phenotypes.image_catalog import \
                maybe_refresh_image_catalog
            maybe_refresh_image_catalog(db)
        except Exception as exc:
            app.logger.warning("Unable to refresh the marker image catalog: %s", exc)

    @app.errorhandler(413)
    def handle_payload_too_large(error):
        """Turn an oversized upload into a flash, but only where that fits.

        MAX_CONTENT_LENGTH is 8MB app-wide, so this fires for data imports
        and API calls too. Redirecting those -- and telling them about a 2MB
        image limit they never hit -- would be wrong, so anything outside the
        markers blueprint keeps the plain 413.
        """
        if request.blueprint != "markers":
            return error
        flash("That image is too large. Images must be 2 MB or smaller.", "danger")
        return redirect(request.referrer or url_for("main.home"))

    with app.app_context():
        # Fail fast: a malformed shipped catalog must not become a 500 on a
        # random later request, and must never produce partial predictions.
        from flymanager.utils.phenotypes.marker_catalog import get_catalog

        get_catalog()

        # Seed bytes and metadata share Mongo's backup domain. A corrupt or
        # unreadable committed seed is a startup failure, just like catalog.json.
        from flymanager.utils.phenotypes.image_catalog import refresh_image_catalog
        from flymanager.utils.phenotypes.image_seed import load_image_seed
        from flymanager.utils.phenotypes.image_store import GridFSImageStore

        # Bootstrap the settings singleton before anything $inc-upserts a
        # revision counter into it. get_settings backfills missing keys now,
        # but creating the document from its owner keeps the ordering
        # obvious rather than relying on the repair path on every boot.
        from flymanager.utils.mongo.settings import get_settings

        get_settings(db)

        load_image_seed(db, GridFSImageStore(db))
        refresh_image_catalog(db, force=True)

        # --- Import and Register Blueprints ---
        from flymanager.app.routes import (auth, cross, data, flip, jobs,
                                           main, markers, settings, stock, tray)

        app.register_blueprint(main.bp)
        app.register_blueprint(auth.bp)
        app.register_blueprint(stock.bp, url_prefix="/stock")
        app.register_blueprint(cross.bp, url_prefix="/cross")
        app.register_blueprint(flip.bp, url_prefix="/flip")
        app.register_blueprint(data.bp, url_prefix="/data")
        app.register_blueprint(tray.bp, url_prefix="/tray")
        app.register_blueprint(settings.bp)
        app.register_blueprint(jobs.bp)
        app.register_blueprint(markers.bp)

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

            try:
                preload_flybase_chromosome_lookup_tables()
            except Exception as exc:
                app.logger.warning(
                    "Unable to preload FlyBase chromosome lookup tables during startup: %s",
                    exc,
                )

        # --- Import Services (to ensure they are loaded) ---
        from flymanager.app.services import bloomington as bloomington_service
        from flymanager.app.services import email as email_service
        from flymanager.app.services import flybase as flybase_service
        from flymanager.app.services import state_backup as state_backup_service
        from flymanager.app.services import scanner as scanner_service
        from flymanager.app.services import scheduler as scheduler_service

        # --- Initialize Scheduler ---
        if env_flag("ENABLE_SCHEDULER", True) and not scheduler.running:
            # Each job below shares its lock key with its matching admin
            # "run now" route. For the two monthly jobs, an admin-triggered
            # run leaves a succeeded/failed history record under that key
            # until its TTL expires; if the monthly cron tick lands while
            # that history record is still present, it will see the key as
            # taken and skip the tick (logged, not an error) -- expected,
            # since the data was just refreshed manually.
            # Add scheduled job using the function from services
            scheduler.add_job(
                id="daily_flip_reminder_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="maintenance:daily-flip-reminder",
                    label="Daily flip reminder",
                    ttl_seconds=1800,
                    func=scheduler_service.schedule_daily_flip_reminders,
                ),
                trigger="cron",
                hour=8,
                minute=0,
                args=[app],  # Pass the app instance to the scheduled function
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            # Add monthly FlyBase reference refresh job (1st day of each month at 1:30 AM)
            scheduler.add_job(
                id="monthly_flybase_reference_refresh_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="maintenance:flybase-reference-refresh",
                    label="Monthly FlyBase reference refresh",
                    ttl_seconds=3600,
                    func=flybase_service.update_flybase_reference_data,
                ),
                trigger="cron",
                day=1,
                hour=1,
                minute=30,
                args=[app],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            # Add monthly legacy Bloomington compatibility refresh job (1st day of each month at 2 AM)
            scheduler.add_job(
                id="monthly_bloomington_update_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="maintenance:bloomington-stock-refresh",
                    label="Monthly Bloomington compatibility refresh",
                    ttl_seconds=3600,
                    func=bloomington_service.update_bloomington_stock_data,
                ),
                trigger="cron",
                day=1,
                hour=2,
                minute=0,
                args=[app],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            # Daily state backup (02:15, after the nightly quiet period).
            #
            # This is the half of the backup story mongodump does not cover:
            # the files on disk and the process environment, which is the only
            # place SECRET_KEY and the SMTP/admin credentials exist. It lives
            # here rather than in a DSM Task Scheduler entry so the schedule
            # travels with the stack instead of sitting outside it, invisible
            # to the compose file and easy to lose in a migration.
            #
            # The MONGO dump deliberately does NOT move here. It runs in the
            # mongo-backup container on its own loop, in a separate failure
            # domain: when the app crash-looped on 2026-08-31 that container
            # kept taking backups throughout. A backup system must not depend
            # on the health of the thing it is backing up.
            scheduler.add_job(
                id="daily_state_backup_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="maintenance:state-backup",
                    label="Daily state backup",
                    ttl_seconds=1800,
                    func=state_backup_service.run_state_backup,
                ),
                trigger="cron",
                hour=2,
                minute=15,
                args=[app],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
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
