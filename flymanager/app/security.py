import os
import secrets
from datetime import datetime

from flask import current_app, g, request, session
from flask_limiter import Limiter
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def _rate_limit_key():
    username = session.get("username")
    remote_addr = request.remote_addr or "unknown"
    if username:
        return f"{username}:{remote_addr}"
    return remote_addr


limiter = Limiter(
    key_func=_rate_limit_key,
    default_limits=[],
)


def generate_csp_nonce():
    nonce = getattr(g, "csp_nonce", None)
    if nonce is None:
        nonce = secrets.token_urlsafe(16)
        g.csp_nonce = nonce
    return nonce


def build_content_security_policy():
    nonce = generate_csp_nonce()
    script_sources = [
        "'self'",
        f"'nonce-{nonce}'",
        "https://cdn.jsdelivr.net",
        "https://cdn.socket.io",
    ]
    style_sources = [
        "'self'",
        f"'nonce-{nonce}'",
        "https://cdn.jsdelivr.net",
    ]
    font_sources = [
        "'self'",
        "data:",
    ]
    directives = {
        "default-src": ["'self'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'self'"],
        "img-src": ["'self'", "data:", "https:"],
        "script-src": script_sources,
        "style-src": style_sources,
        "style-src-elem": style_sources,
        "font-src": font_sources,
        "connect-src": ["'self'", "ws:", "wss:"],
        "object-src": ["'none'"],
    }
    return "; ".join(
        f"{directive} {' '.join(values)}" for directive, values in directives.items()
    )


def build_permissions_policy():
    camera_policy = "camera=(self)" if current_app.config.get("ENABLE_CAMERA_SCANNER", True) else "camera=()"
    return ", ".join(
        [
            camera_policy,
            "geolocation=()",
            "microphone=()",
            "payment=()",
            "usb=()",
        ]
    )


def parse_allowed_origins(raw_value):
    if not raw_value:
        return None

    origins = [origin.strip() for origin in raw_value.split(",") if origin.strip()]
    return origins or None


def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        build_permissions_policy(),
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", build_content_security_policy())
    return response


def get_json_payload():
    if not request.is_json:
        raise ValueError("Request body must be JSON.")

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object.")

    return payload


def normalize_text(value, *, field_name, max_length, allow_empty=False):
    text = (value or "").strip()
    if not text and not allow_empty:
        raise ValueError(f"{field_name} is required.")
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters long.")
    return text


def normalize_optional_text(value, *, field_name, max_length):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters long.")
    return text


def require_confirmation(value, *, action_name):
    normalized_value = str(value or "").strip().lower()
    if normalized_value != "confirmed":
        raise ValueError(f"Please confirm the {action_name} before submitting.")
    return True


def normalize_identifier_list(value, *, field_name, max_items=100, max_length=64):
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")
    if not value:
        raise ValueError(f"At least one {field_name[:-1] if field_name.endswith('s') else field_name} is required.")
    if len(value) > max_items:
        raise ValueError(f"{field_name} cannot contain more than {max_items} entries.")

    normalized_values = []
    for item in value:
        normalized = normalize_text(
            item,
            field_name=field_name[:-1] if field_name.endswith("s") else field_name,
            max_length=max_length,
        )
        normalized_values.append(normalized)

    return normalized_values


def parse_iso_datetime(value, *, field_name):
    if not value:
        return datetime.now()

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name} format.") from exc


def parse_int_value(value, *, field_name, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc

    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}.")
    return parsed


def log_security_event(message, *args):
    current_app.logger.info("security_event=" + message, *args)