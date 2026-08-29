"""Covers the new unified label-printing route that lets a mixed cart of
stocks and crosses print together in one PDF/print job (feature request:
"share the cart between explorers so we can print both at once").

generate_label_pdf() already accepts a per-item item_type list - the route
just needs to merge accessible stocks + crosses by uid/type, in the order
the shared cart sends them, then hand a single combined list to it.
"""
import os
import shutil

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


def test_generate_labels_merges_stock_and_cross_by_tray_position(monkeypatch):
    app = _make_app(monkeypatch)
    db = _db()

    captured = {}

    def fake_generate_label_pdf(filename, user_initial, selected_items, item_type,
                                 num_blank, num_labels, *args, **kwargs):
        captured["items"] = selected_items
        captured["types"] = item_type

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.main.db", db), patch(
            "flymanager.app.routes.main.generate_label_pdf", side_effect=fake_generate_label_pdf
        ):
            response = client.post(
                "/generate_labels",
                data={
                    "selected_uids": "s1,x1",
                    "item_types": "stock,cross",
                    "quantities": "1,1",
                    "blank_spaces": "0",
                },
            )

    assert response.status_code == 302
    # x1 sits in T1-1, s1 in T2-9 - the cross must print first because the
    # merged list is sorted by (TrayID, TrayPosition) across both types.
    assert captured["types"] == ["cross", "stock"]
    assert [item["UniqueID"] for item in captured["items"]] == ["x1", "s1"]


def test_generate_labels_redirect_target_actually_exists(monkeypatch):
    app = _make_app(monkeypatch)
    db = _db()

    labels_dir = os.path.join(app.static_folder, "generated_labels")
    generated_before = set(os.listdir(labels_dir)) if os.path.isdir(labels_dir) else set()

    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "alice"

            with patch("flymanager.app.routes.main.db", db):
                response = client.post(
                    "/generate_labels",
                    data={
                        "selected_uids": "s1,x1",
                        "item_types": "stock,cross",
                        "quantities": "1,1",
                        "blank_spaces": "0",
                    },
                )

        assert response.status_code == 302
        redirect_target = response.headers["Location"]

        with app.test_client() as client:
            follow_up = client.get(redirect_target)

        assert follow_up.status_code == 200
    finally:
        if os.path.isdir(labels_dir):
            for name in set(os.listdir(labels_dir)) - generated_before:
                os.remove(os.path.join(labels_dir, name))
        if os.path.isdir("temp"):
            shutil.rmtree("temp")


def test_generate_labels_rejects_mismatched_field_lengths(monkeypatch):
    app = _make_app(monkeypatch)
    db = _db()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.main.db", db):
            response = client.post(
                "/generate_labels",
                data={
                    "selected_uids": "s1,x1",
                    "item_types": "stock",
                    "quantities": "1,1",
                    "blank_spaces": "0",
                },
                follow_redirects=True,
            )

    assert response.status_code == 200
    assert "Mismatch" in response.get_data(as_text=True)
