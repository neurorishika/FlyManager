"""The app must refuse to boot on the committed placeholder SECRET_KEY.

scripts/install-production.sh copied .env.example verbatim and never generated
a key, so a documented production install ran with
`SECRET_KEY=replace-with-a-long-random-string` -- a value published in this
repository. The existing guard only rejected an EMPTY key, so the placeholder
passed it and every session cookie became forgeable, including the admin's.

The installer is fixed too, but the guard is what makes the failure loud for
anyone whose .env predates that fix.
"""
from unittest.mock import patch

import pytest

from flymanager.app import PLACEHOLDER_SECRET_KEY


def _settings_payload():
    return {"lab_info": {"lab_name": "Test Lab", "admin_name": "Admin",
                         "admin_email": "admin@example.com"},
            "theme": {"accent_color": "#0055aa", "dark_mode": False}}


def _create(monkeypatch, **env):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from flymanager.app import create_app
    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        return create_app()


def test_the_placeholder_is_rejected_on_a_public_domain(monkeypatch):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _create(monkeypatch, SECRET_KEY=PLACEHOLDER_SECRET_KEY,
                FLYMANAGER_DOMAIN="flymanager.example.org")


def test_the_placeholder_is_rejected_with_secure_cookies(monkeypatch):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _create(monkeypatch, SECRET_KEY=PLACEHOLDER_SECRET_KEY,
                SESSION_COOKIE_SECURE="1")


def test_surrounding_whitespace_does_not_smuggle_the_placeholder_through(monkeypatch):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _create(monkeypatch, SECRET_KEY="  " + PLACEHOLDER_SECRET_KEY + "  ",
                FLYMANAGER_DOMAIN="flymanager.example.org")


def test_an_empty_key_is_still_rejected(monkeypatch):
    """The original guard's behaviour must survive."""
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _create(monkeypatch, SECRET_KEY="", FLYMANAGER_DOMAIN="flymanager.example.org")


def test_a_real_key_boots(monkeypatch):
    app = _create(monkeypatch, SECRET_KEY="0" * 64,
                  FLYMANAGER_DOMAIN="flymanager.example.org")
    assert app is not None


def test_local_development_on_the_placeholder_still_boots(monkeypatch):
    """No domain and no secure cookies means a local dev box; blocking that
    would break `docker compose up` for anyone following the README."""
    app = _create(monkeypatch, SECRET_KEY=PLACEHOLDER_SECRET_KEY)
    assert app is not None
