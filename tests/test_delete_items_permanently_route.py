"""Covers the new unified permanent-delete route that lets a mixed cart of
stocks and crosses (both with Status "No longer maintained") be deleted in
one call, replacing the two separate per-type routes
(stock.delete_stock_permanently / cross.delete_cross_permanently).
"""
import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from unittest.mock import patch

from tests.mongo_fakes import FakeDatabase


def _stock(uid, **overrides):
    stock = {"UniqueID": uid, "User": "alice", "Status": "No longer maintained", "Name": uid}
    stock.update(overrides)
    return stock


def _cross(uid, **overrides):
    cross = {"UniqueID": uid, "User": "alice", "Status": "No longer maintained", "Name": uid}
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


def test_delete_items_permanently_handles_mixed_stock_and_cross(monkeypatch):
    app = _make_app(monkeypatch)
    db = FakeDatabase({
        "stocks": [_stock("s1"), _stock("s2", Status="Healthy")],
        "crosses": [_cross("x1")],
    })

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.main.db", db):
            response = client.post(
                "/delete_items_permanently",
                json={
                    "uniqueIDs": ["s1", "s2", "x1"],
                    "itemTypes": ["stock", "stock", "cross"],
                },
            )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["deleted"] == 2
    assert payload["skipped"] == 1

    assert db["stocks"].find_one({"UniqueID": "s1"}) is None
    assert db["stocks"].find_one({"UniqueID": "s2"}) is not None
    assert db["crosses"].find_one({"UniqueID": "x1"}) is None


def test_delete_items_permanently_rejects_mismatched_lengths(monkeypatch):
    app = _make_app(monkeypatch)
    db = FakeDatabase({"stocks": [_stock("s1")], "crosses": []})

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.main.db", db):
            response = client.post(
                "/delete_items_permanently",
                json={"uniqueIDs": ["s1"], "itemTypes": []},
            )

    assert response.status_code == 400
    assert response.get_json()["success"] is False
