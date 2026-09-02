"""Covers the two mail features added after the 2026-09 outage, where a
redeploy dropped MAIL_SUPPRESS_SEND=0 and SMTP_SENDER from the Portainer
stack and every reminder silently no-opped for days.

Two things had to become true:
  * mail misconfiguration must be *visible* -- reported per setting, and a
    send failure must surface the SMTP error rather than being swallowed.
  * the reminder must carry the labels for the vials it is reminding about.
"""
import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from unittest.mock import patch

from tests.mongo_fakes import FakeDatabase


def _make_app(monkeypatch, **env):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", env.pop("MAIL_SUPPRESS_SEND", "1"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from flymanager.app import create_app

    with patch("flymanager.app.get_settings", return_value={
        "lab_info": {"lab_name": "Test Lab", "admin_name": "Admin", "admin_email": "a@b.com"},
        "theme": {"accent_color": "#0055aa", "dark_mode": False},
    }):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _db():
    return FakeDatabase({
        "users": [
            {"Username": "admin", "Initials": "AB", "Email": "admin@example.com"},
        ],
        "stocks": [],
        "crosses": [],
    })


# --- config diagnostics -------------------------------------------------

def test_mail_config_status_flags_the_suppress_switch(monkeypatch):
    """The exact production failure: everything configured, but the
    suppress flag left at its compose-file default of 1."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="1", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )

    from flymanager.app.services.email import mail_config_status

    with app.app_context():
        status = mail_config_status()

    assert status["suppressed"] is True
    assert status["ready"] is False
    assert "MAIL_SUPPRESS_SEND" in " ".join(status["problems"])


def test_mail_config_status_flags_the_empty_sender(monkeypatch):
    """The second half of the outage: sender came back empty from the
    redeploy, which alone stops every send."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="",
    )

    from flymanager.app.services.email import mail_config_status

    with app.app_context():
        status = mail_config_status()

    assert status["ready"] is False
    assert any("SMTP_SENDER" in problem for problem in status["problems"])


def test_mail_config_status_is_ready_when_fully_configured(monkeypatch):
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )

    from flymanager.app.services.email import mail_config_status

    with app.app_context():
        status = mail_config_status()

    assert status["ready"] is True
    assert status["problems"] == []


def test_mail_config_status_never_exposes_the_password(monkeypatch):
    """The readout renders on an admin page; it reports presence only."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com", SMTP_PASSWORD="hunter2-secret",
    )

    from flymanager.app.services.email import mail_config_status

    with app.app_context():
        status = mail_config_status()

    assert "hunter2-secret" not in repr(status)
    assert status["password_set"] is True


# --- the test-email route ----------------------------------------------

def test_test_email_route_requires_admin(monkeypatch):
    app = _make_app(monkeypatch)
    db = FakeDatabase({
        "users": [{"Username": "bob", "Email": "bob@example.com"}],
        "stocks": [], "crosses": [],
    })

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "bob"
        with patch("flymanager.app.routes.settings.db", db), patch(
            "flymanager.app.routes.auth.db", db
        ):
            response = client.post("/settings/send-test-email")

    assert response.status_code in (302, 403)


def test_test_email_route_reports_config_problem_instead_of_claiming_success(monkeypatch):
    """The old /test_send_reminder returned success while sending nothing.
    This route must say why it did not send."""
    app = _make_app(monkeypatch, MAIL_SUPPRESS_SEND="1")
    db = _db()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"
        with patch("flymanager.app.routes.settings.db", db), patch(
            "flymanager.app.routes.auth.db", db
        ), patch("flymanager.app.services.email.db", db):
            response = client.post("/settings/send-test-email", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "MAIL_SUPPRESS_SEND" in body


def test_test_email_route_surfaces_the_smtp_error(monkeypatch):
    """An auth failure or refused connection must reach the admin, not a
    generic 500."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )
    db = _db()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"
        with patch("flymanager.app.routes.settings.db", db), patch(
            "flymanager.app.routes.auth.db", db
        ), patch("flymanager.app.services.email.db", db), patch(
            "flymanager.app.services.email.mail.send",
            side_effect=OSError("535 authentication failed"),
        ):
            response = client.post("/settings/send-test-email", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "535 authentication failed" in body


def test_test_email_route_sends_only_to_the_requesting_admin(monkeypatch):
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )
    db = FakeDatabase({
        "users": [
            {"Username": "admin", "Email": "admin@example.com"},
            {"Username": "bob", "Email": "bob@example.com"},
        ],
        "stocks": [], "crosses": [],
    })
    sent = []

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"
        with patch("flymanager.app.routes.settings.db", db), patch(
            "flymanager.app.routes.auth.db", db
        ), patch("flymanager.app.services.email.db", db), patch(
            "flymanager.app.services.email.mail.send", side_effect=sent.append
        ):
            client.post("/settings/send-test-email", follow_redirects=True)

    assert len(sent) == 1
    assert sent[0].recipients == ["admin@example.com"]


# --- label attachment on the reminder ----------------------------------

def _schedule(today, overdue):
    return {
        overdue: ["Stock: s1 (ID: s1, T1 - 1)"],
        today: ["Cross: x1 (ID: x1, T1 - 2)"],
    }


def test_reminder_attaches_labels_for_due_and_overdue(monkeypatch):
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )
    db = _db()
    sent = []
    requested = {}

    def fake_build(username, dates, db_arg, **kwargs):
        requested["dates"] = list(dates)
        return b"%PDF-labels", 2

    from flymanager.app.services.email import send_flip_reminder_email

    with app.app_context():
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        overdue = "2000-01-01"

        with patch("flymanager.app.services.email.db", db), patch(
            "flymanager.app.services.email.get_flip_schedule",
            return_value=_schedule(today, overdue),
        ), patch(
            "flymanager.app.services.email.get_user_email",
            return_value="alice@example.com",
        ), patch(
            "flymanager.app.services.email.build_label_pdf", side_effect=fake_build
        ), patch(
            "flymanager.app.services.email.mail.send", side_effect=sent.append
        ):
            send_flip_reminder_email("alice")

    assert len(sent) == 1
    attachments = sent[0].attachments
    assert len(attachments) == 1
    assert attachments[0].content_type == "application/pdf"
    assert attachments[0].data == b"%PDF-labels"
    # Both the overdue date and today must be covered.
    assert overdue in requested["dates"] and today in requested["dates"]


def test_reminder_sends_without_attachment_when_nothing_is_due(monkeypatch):
    """The "no flips today" email should not carry an empty PDF."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )
    db = _db()
    sent = []

    from flymanager.app.services.email import send_flip_reminder_email

    with app.app_context():
        with patch("flymanager.app.services.email.db", db), patch(
            "flymanager.app.services.email.get_flip_schedule", return_value={}
        ), patch(
            "flymanager.app.services.email.get_user_email",
            return_value="alice@example.com",
        ), patch(
            "flymanager.app.services.email.mail.send", side_effect=sent.append
        ):
            send_flip_reminder_email("alice")

    assert len(sent) == 1
    assert sent[0].attachments == []


def test_reminder_still_sends_when_label_generation_fails(monkeypatch):
    """A broken label must never cost the user their reminder."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )
    db = _db()
    sent = []

    from flymanager.app.services.email import send_flip_reminder_email

    with app.app_context():
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y-%m-%d")

        with patch("flymanager.app.services.email.db", db), patch(
            "flymanager.app.services.email.get_flip_schedule",
            return_value={today: ["Cross: x1 (ID: x1, T1 - 2)"]},
        ), patch(
            "flymanager.app.services.email.get_user_email",
            return_value="alice@example.com",
        ), patch(
            "flymanager.app.services.email.build_label_pdf",
            side_effect=RuntimeError("reportlab exploded"),
        ), patch(
            "flymanager.app.services.email.mail.send", side_effect=sent.append
        ):
            send_flip_reminder_email("alice")

    assert len(sent) == 1
    assert sent[0].attachments == []


def test_reminder_skips_an_oversized_attachment(monkeypatch):
    """Too large for SMTP: send the reminder without it rather than have the
    whole message bounce."""
    app = _make_app(
        monkeypatch, MAIL_SUPPRESS_SEND="0", SMTP_SERVER="mail.example.com",
        SMTP_SENDER="lab@example.com",
    )
    db = _db()
    sent = []

    from flymanager.app.services.email import (MAX_ATTACHMENT_BYTES,
                                               send_flip_reminder_email)

    with app.app_context():
        import datetime as _dt
        today = _dt.datetime.now().strftime("%Y-%m-%d")

        with patch("flymanager.app.services.email.db", db), patch(
            "flymanager.app.services.email.get_flip_schedule",
            return_value={today: ["Cross: x1 (ID: x1, T1 - 2)"]},
        ), patch(
            "flymanager.app.services.email.get_user_email",
            return_value="alice@example.com",
        ), patch(
            "flymanager.app.services.email.build_label_pdf",
            return_value=(b"x" * (MAX_ATTACHMENT_BYTES + 1), 1),
        ), patch(
            "flymanager.app.services.email.mail.send", side_effect=sent.append
        ):
            send_flip_reminder_email("alice")

    assert len(sent) == 1
    assert sent[0].attachments == []


def test_admin_page_renders_the_mail_status_readout(monkeypatch):
    """The readout is the whole point: an admin must see the broken setting
    without clicking anything."""
    app = _make_app(monkeypatch, MAIL_SUPPRESS_SEND="1")
    db = _db()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"
        with patch("flymanager.app.routes.settings.db", db), patch(
            "flymanager.app.routes.auth.db", db
        ), patch("flymanager.app.routes.settings.get_settings", return_value={
            "lab_info": {"lab_name": "Test Lab", "admin_name": "Admin",
                         "admin_email": "a@b.com"},
            "theme": {"accent_color": "#0055aa", "dark_mode": False},
        }), patch(
            "flymanager.app.routes.settings.get_user_profiles", return_value=[]
        ), patch(
            "flymanager.app.routes.settings.flybase_service."
            "get_flybase_reference_status", return_value={}
        ), patch(
            "flymanager.app.routes.settings.check_force_refresh_cooldown",
            return_value=(True, None),
        ):
            response = client.get("/settings")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Email Delivery" in body
    assert "Send Test Email To Myself" in body
    assert "Email is not being sent" in body
