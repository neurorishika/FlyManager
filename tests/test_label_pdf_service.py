"""Covers the label-building helper extracted out of the flip route so the
scheduled reminder email can attach the same PDF the UI produces.

The helper must work with no request context (the reminder runs under the
scheduler, where there is no `session` and `url_for` raises), so it takes
username and db explicitly and returns bytes rather than writing into
static/generated_labels.
"""
import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from unittest.mock import patch

from tests.mongo_fakes import FakeDatabase


def _stock(uid, tray_id="T1", position="1", **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "TrayID": tray_id, "TrayPosition": position,
        "Name": uid, "Genotype": "w[1118]", "AltReference": "", "Status": "Healthy",
        "VialLifetime": "28", "FlipFrequency": "7",
    }
    stock.update(overrides)
    return stock


def _cross(uid, tray_id="T1", position="2", **overrides):
    cross = {
        "UniqueID": uid, "User": "alice", "TrayID": tray_id, "TrayPosition": position,
        "Name": uid, "MaleGenotype": "w[1118]", "FemaleGenotype": "w[1118]",
        "Status": "Healthy", "VialLifetime": "14", "FlipFrequency": "7",
        "MaleUniqueID": "m1", "FemaleUniqueID": "f1",
    }
    cross.update(overrides)
    return cross


def _make_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")

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
        "stocks": [_stock("s1", tray_id="T2", position="9")],
        "crosses": [_cross("x1", tray_id="T1", position="1")],
        "users": [{"Username": "alice", "Initials": "AB"}],
    })


def _schedule():
    return {
        "2026-09-01": ["Stock: s1 (ID: s1, T2 - 9)"],
        "2026-09-02": ["Cross: x1 (ID: x1, T1 - 1)"],
    }


def test_build_label_pdf_returns_bytes_for_the_requested_dates(monkeypatch):
    """The helper renders a real PDF and hands back its bytes, so the email
    service can attach it without touching the filesystem."""
    app = _make_app(monkeypatch)
    db = _db()

    from flymanager.app.services.labels import build_label_pdf

    with app.app_context():
        with patch("flymanager.app.services.labels.db", db), patch(
            "flymanager.app.services.labels.get_flip_schedule", return_value=_schedule()
        ):
            pdf_bytes, count = build_label_pdf("alice", ["2026-09-02"], db)

    assert count == 1
    assert pdf_bytes.startswith(b"%PDF")


def test_build_label_pdf_merges_multiple_dates_sorted_by_tray(monkeypatch):
    """Overdue dates and today are rendered into one PDF, ordered by tray
    position across both dates -- the print stack follows the trays, not
    the dates."""
    app = _make_app(monkeypatch)
    db = _db()
    captured = {}

    def fake_generate_label_pdf(filename, user_initial, selected_items, item_type,
                                num_blank, num_labels, *args, **kwargs):
        captured["items"] = selected_items
        captured["types"] = item_type
        captured["initials"] = user_initial
        with open(filename, "wb") as handle:
            handle.write(b"%PDF-fake")

    from flymanager.app.services.labels import build_label_pdf

    with app.app_context():
        with patch("flymanager.app.services.labels.db", db), patch(
            "flymanager.app.services.labels.get_flip_schedule", return_value=_schedule()
        ), patch(
            "flymanager.app.services.labels.generate_label_pdf",
            side_effect=fake_generate_label_pdf,
        ):
            pdf_bytes, count = build_label_pdf(
                "alice", ["2026-09-01", "2026-09-02"], db
            )

    assert count == 2
    assert pdf_bytes == b"%PDF-fake"
    # x1 sits in T1-1, s1 in T2-9, so the cross prints first even though its
    # date comes second.
    assert captured["types"] == ["cross", "stock"]
    assert [item["UniqueID"] for item in captured["items"]] == ["x1", "s1"]
    assert captured["initials"] == "AB"


def test_build_label_pdf_returns_none_when_nothing_scheduled(monkeypatch):
    """No items means no attachment -- the caller must be able to tell
    without catching an exception."""
    app = _make_app(monkeypatch)
    db = _db()

    from flymanager.app.services.labels import build_label_pdf

    with app.app_context():
        with patch("flymanager.app.services.labels.db", db), patch(
            "flymanager.app.services.labels.get_flip_schedule", return_value=_schedule()
        ):
            pdf_bytes, count = build_label_pdf("alice", ["2026-12-25"], db)

    assert pdf_bytes is None
    assert count == 0


def test_build_label_pdf_works_without_a_request_context(monkeypatch):
    """The scheduler calls this with no request in flight. Anything reaching
    for `session` or `url_for` would blow up here."""
    app = _make_app(monkeypatch)
    db = _db()

    from flymanager.app.services.labels import build_label_pdf

    # Only an app context -- deliberately no test_request_context.
    with app.app_context():
        with patch("flymanager.app.services.labels.db", db), patch(
            "flymanager.app.services.labels.get_flip_schedule", return_value=_schedule()
        ):
            pdf_bytes, count = build_label_pdf("alice", ["2026-09-02"], db)

    assert count == 1
    assert pdf_bytes.startswith(b"%PDF")


def test_build_label_pdf_leaves_no_temp_file_behind(monkeypatch, tmp_path):
    """The helper renders through a temp file; it must clean up after itself
    rather than growing a directory nobody prunes."""
    app = _make_app(monkeypatch)
    db = _db()

    import tempfile

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(scratch))

    from flymanager.app.services.labels import build_label_pdf

    with app.app_context():
        with patch("flymanager.app.services.labels.db", db), patch(
            "flymanager.app.services.labels.get_flip_schedule", return_value=_schedule()
        ):
            build_label_pdf("alice", ["2026-09-02"], db)

    assert list(scratch.iterdir()) == []


# --- the route that was refactored onto the helper ----------------------

def test_generate_labels_for_day_route_returns_a_real_pdf(monkeypatch):
    """The /flip route was rewritten to call build_label_pdf and write the
    bytes itself, so its redirect target must still be a fetchable PDF."""
    import os

    app = _make_app(monkeypatch)
    db = _db()

    labels_dir = os.path.join(app.static_folder, "generated_labels")
    generated_before = set(os.listdir(labels_dir)) if os.path.isdir(labels_dir) else set()

    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "alice"

            with patch("flymanager.app.routes.flip.db", db), patch(
                "flymanager.app.routes.flip.get_flip_schedule", return_value=_schedule()
            ), patch(
                "flymanager.app.services.labels.db", db
            ), patch(
                "flymanager.app.services.labels.get_flip_schedule",
                return_value=_schedule(),
            ):
                response = client.post(
                    "/flip/generate_labels_for_day",
                    data={"date": "2026-09-02", "blank_spaces": "0"},
                )

            assert response.status_code == 302
            redirect_target = response.headers["Location"]
            assert "generated_labels" in redirect_target

            with app.test_client() as client:
                follow_up = client.get(redirect_target)

            assert follow_up.status_code == 200
            assert follow_up.data.startswith(b"%PDF")
    finally:
        if os.path.isdir(labels_dir):
            for name in set(os.listdir(labels_dir)) - generated_before:
                os.remove(os.path.join(labels_dir, name))


def test_generate_labels_for_day_route_reports_an_empty_day(monkeypatch):
    app = _make_app(monkeypatch)
    db = _db()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.flip.db", db), patch(
            "flymanager.app.routes.flip.get_flip_schedule", return_value=_schedule()
        ):
            response = client.post(
                "/flip/generate_labels_for_day",
                data={"date": "2026-12-25", "blank_spaces": "0"},
                follow_redirects=True,
            )

    assert response.status_code == 200
    assert "No items scheduled for 2026-12-25" in response.get_data(as_text=True)


def test_generate_labels_for_day_route_rejects_a_bad_date(monkeypatch):
    app = _make_app(monkeypatch)
    db = _db()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.flip.db", db):
            response = client.post(
                "/flip/generate_labels_for_day",
                data={"date": "not-a-date", "blank_spaces": "0"},
                follow_redirects=True,
            )

    assert response.status_code == 200
    assert "Invalid date format" in response.get_data(as_text=True)
