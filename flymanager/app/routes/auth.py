# flymanager/app/routes/auth.py
import re
from functools import wraps

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from flymanager.app import db
from flymanager.app.security import limiter, log_security_event
from flymanager.app.services.email import (mail_is_configured,
                                           send_password_reset_email)
from flymanager.utils.mongo import (add_user, consume_password_reset_token,
                                    create_password_reset_token,
                                    get_reset_token_username, get_user_crosses,
                                    get_user_email, get_user_stocks,
                                    update_cross_vials, update_stock_vials,
                                    verify_user_password, write_activity)
from flymanager.utils.phenotypes.flybase_pipeline import \
    flybase_phenotype_cache_status
from flymanager.utils.phenotypes.predictor import (get_cached_cross_phenotype,
                                                    get_cached_stock_phenotype)

bp = Blueprint("auth", __name__, url_prefix="/auth")

MIN_PASSWORD_LENGTH = 12
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
INITIALS_PATTERN = re.compile(r"^[A-Za-z0-9]{1,6}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def rotate_session_identifier():
    """Rotate the server-side session identifier when auth state changes."""
    regenerate = getattr(current_app.session_interface, "regenerate", None)
    if callable(regenerate) and session:
        regenerate(session)


def validate_password(password):
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
    return None


def validate_registration_input(username, password, initials, email):
    if not USERNAME_PATTERN.fullmatch(username):
        return "Username must be 3-32 characters and contain only letters, numbers, dots, underscores, or hyphens."
    if not INITIALS_PATTERN.fullmatch(initials):
        return "Initials must be 1-6 letters or numbers."
    password_error = validate_password(password)
    if password_error:
        return password_error
    if email and not EMAIL_PATTERN.fullmatch(email):
        return "Recovery email must be a valid email address."
    return None


# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            flash("Please log in to access this page", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


# Another decorator for making sure the user is equal to admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("username") != "admin":
            flash("Admin access required", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


def _flash_phenotype_cache_staleness_warnings(username, stocks, crosses):
    """Nudge the user, at login, if any phenotype data needs an explicit refresh.

    Phenotype predictions are computed once and persisted; they're never
    silently recomputed on a page view. This is the one place that surfaces
    "you might want to refresh" instead, without blocking anything.
    """
    stale_stock_count = sum(
        1 for stock in stocks if get_cached_stock_phenotype(stock, strict=True) is None
    )
    stale_cross_count = sum(
        1 for cross in crosses if get_cached_cross_phenotype(cross, strict=True) is None
    )
    stale_total = stale_stock_count + stale_cross_count
    if stale_total:
        flash(
            f"{stale_total} of your records have outdated phenotype predictions. "
            "Refresh them from Settings when convenient.",
            "info",
        )

    if username == "admin":
        evidence_status = flybase_phenotype_cache_status()
        if evidence_status["stale"]:
            state = "missing" if not evidence_status["built"] else "out of date"
            flash(
                f"The FlyBase phenotype evidence index is {state}. "
                "Rebuild it from Settings to keep phenotype predictions accurate.",
                "warning",
            )


@bp.route("/login", methods=["POST", "GET"])
@limiter.limit("20 per minute")
def login():
    if session.get("username"):
        return redirect(url_for("main.home"))  # Use url_for
    message = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password")

        if not username or not password:
            message = "Username and password are required."
        else:
            authenticated, migrated = verify_user_password(username, password, db=db)
            if authenticated:
                session.clear()
                session["username"] = username
                session.permanent = True
                rotate_session_identifier()
                stocks, crosses = [], []
                try:
                    stocks = get_user_stocks(username, db)
                    for stock in stocks:
                        update_stock_vials(stock, username, db)
                    crosses = get_user_crosses(username, db)
                    for cross in crosses:
                        update_cross_vials(cross, username, db)
                    write_activity(username, "Logged in", db)
                    if migrated:
                        write_activity(username, "Password hash upgraded", db)
                except Exception as e:
                    current_app.logger.exception(
                        "Error updating post-login state for %s: %s", username, e
                    )

                try:
                    _flash_phenotype_cache_staleness_warnings(username, stocks, crosses)
                except Exception as e:
                    current_app.logger.exception(
                        "Error checking phenotype cache staleness for %s: %s", username, e
                    )

                return redirect(url_for("main.home"))

            log_security_event("failed_login username=%s", username)
            current_app.logger.warning("Failed login attempt for username '%s'", username)
            message = "Invalid username or password."

    # For GET request or failed POST
    return render_template("auth/login.html", message=message)


@bp.route("/register", methods=["POST", "GET"])
@limiter.limit("10 per hour")
def register():
    if session.get("username"):
        return redirect(url_for("main.home"))  # Use url_for

    message = None
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password")
        initials = (request.form.get("initials") or "").strip().upper()
        email = (request.form.get("email") or "").strip().lower()

        if not username or not password or not initials:
            message = "Username, password, and initials are required."
        else:
            validation_error = validate_registration_input(
                username, password, initials, email
            )
            if validation_error:
                return render_template(
                    "auth/register.html",
                    message=validation_error,
                    form_data={
                        "username": username,
                        "initials": initials,
                        "email": email,
                    },
                )
            try:
                if add_user(username, password, initials, db=db, email=email):
                    flash("Registration complete. You can log in now.", "success")
                    return redirect(url_for("auth.login"))  # Use url_for
                else:
                    message = "Username or recovery email already exists."
            except Exception as e:
                current_app.logger.exception(
                    "Error during registration for %s: %s", username, e
                )
                message = "An error occurred during registration."

    return render_template("auth/register.html", message=message, form_data={})


@bp.route("/forgot_password", methods=["POST", "GET"])
@limiter.limit("8 per hour")
def forgot_password():
    message = None

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if not username:
            message = "Username is required."
        else:
            user_email = get_user_email(username, db)
            if user_email and mail_is_configured():
                token = create_password_reset_token(username, db=db)
                if token:
                    reset_url = url_for("auth.reset_password", token=token, _external=True)
                    send_password_reset_email(username, reset_url)

            log_security_event("password_reset_requested username=%s", username)
            message = (
                "If the account has a recovery email on file, a password reset link has been sent."
            )

    return render_template("auth/forgot_password.html", message=message)


@bp.route("/reset_password/<token>", methods=["GET", "POST"])
@limiter.limit("12 per hour")
def reset_password(token):
    username = get_reset_token_username(token, db=db)
    token_valid = bool(username)
    message = None

    if request.method == "POST":
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not token_valid:
            message = "This password reset link is invalid or has expired."
        elif not new_password or not confirm_password:
            message = "Both password fields are required."
        elif new_password != confirm_password:
            message = "Passwords do not match."
        else:
            password_error = validate_password(new_password)
            if password_error:
                message = password_error
            else:
                reset_username = consume_password_reset_token(token, new_password, db=db)
                if reset_username:
                    log_security_event("password_reset_completed username=%s", reset_username)
                    flash("Password updated. You can log in now.", "success")
                    return redirect(url_for("auth.login"))
                token_valid = False
                message = "This password reset link is invalid or has expired."

    return render_template(
        "auth/reset_password.html",
        message=message,
        token_valid=token_valid,
        username=username,
    )


@bp.route("/logout")
@login_required
def logout():
    username = session.get("username")
    if username:
        try:
            write_activity(username, "Logged out", db)
        except Exception as e:
            current_app.logger.exception(
                "Error logging logout activity for %s: %s", username, e
            )
        rotate_session_identifier()
        session.clear()
    return redirect(url_for("auth.login"))  # Use url_for
