"""Reproduces the reported bug: deleting a tray does not unassign its
stocks/crosses.

Root cause: delete_tray_route() checks occupancy with the URL's tray_id,
which is the tray's UniqueID (e.g. "alice_T1"), but get_tray_occupancy()
queries stocks/crosses by the human-readable TrayID field (e.g. "T1").
Since these never match, the "tray contains items" guard never fires, so
a non-empty tray gets deleted while its stocks/crosses keep pointing at
the now-gone TrayID.
"""
import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from unittest.mock import patch

from tests.mongo_fakes import FakeDatabase


def _tray(user, tray_id, **overrides):
    tray = {
        "UniqueID": f"{user}_{tray_id}", "User": user, "TrayID": tray_id,
        "Name": tray_id, "Rows": 4, "Columns": 10,
    }
    tray.update(overrides)
    return tray


def _stock(uid, tray_id, position, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "TrayID": tray_id, "TrayPosition": position,
        "Name": uid, "Genotype": "w[1118]", "Status": "Healthy",
        "VialLifetime": "28", "FlipFrequency": "7",
    }
    stock.update(overrides)
    return stock


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


def test_delete_tray_route_refuses_to_delete_non_empty_tray(monkeypatch):
    app = _make_app(monkeypatch)

    db = FakeDatabase({
        "trays": [_tray("alice", "T1")],
        "stocks": [_stock("s1", "T1", "1")],
    })

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.tray.db", db):
            response = client.post(
                "/tray/delete_tray/alice_T1", follow_redirects=True
            )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Cannot delete tray" in body

    # The tray must still exist, and the stock must still be able to
    # resolve to it - deletion must not silently orphan the stock.
    assert db["trays"].find_one({"UniqueID": "alice_T1"}) is not None
    assert db["stocks"].find_one({"UniqueID": "s1"})["TrayID"] == "T1"


def test_delete_tray_route_deletes_empty_tray(monkeypatch):
    app = _make_app(monkeypatch)

    db = FakeDatabase({
        "trays": [_tray("alice", "T1")],
        "stocks": [],
    })

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.tray.db", db):
            response = client.post(
                "/tray/delete_tray/alice_T1", follow_redirects=True
            )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "deleted successfully" in body
    assert db["trays"].find_one({"UniqueID": "alice_T1"}) is None
