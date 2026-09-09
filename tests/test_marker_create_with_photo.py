"""A marker can be created with its photo in one go.

Adding a marker and then having to find it again to add its picture is how
markers end up with no picture. It cannot be one request -- the upload
endpoint 404s without an existing marker -- so the marker is created first
and the image attached immediately after, in the same handler.

The ordering matters in one direction only: if the marker saves and the image
is rejected, the marker still saves.
"""
import io
import re
from unittest.mock import patch

from PIL import Image

from flymanager.app import create_app, db


def _png(color=(1, 2, 3)):
    out = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(out, format="PNG")
    return out.getvalue()


def _app(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    app = create_app()
    app.config["TESTING"] = True
    return app


def _csrf(client):
    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        body = client.get("/flip/").get_data(as_text=True)
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', body).group(1)


def _form(key, token, **extra):
    form = {
        "Key": key, "kind": "gene_marker", "csrf_token": token,
        "match.symbol": key, "payload.display_label": key,
        "payload.effect": "test marker", "payload.body_part": "wing",
        "payload.dominance": "dominant", "payload.phenotype_key": key,
    }
    form.update(extra)
    return form


def _cleanup(key):
    entry = db["marker_images"].find_one({"match.markerKeys": key})
    if entry:
        db["marker_images"].delete_one({"imageId": entry["imageId"]})
    db["marker_definitions"].delete_one({"Key": key})
    return entry


def test_creating_with_a_photo_attaches_it(monkeypatch):
    app = _app(monkeypatch)
    key = "zzphoto"
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["username"] = "photo-test-user"
            token = _csrf(client)
            with patch("flymanager.app.routes.markers.enqueue_job"):
                response = client.post("/markers", data=_form(
                    key, token,
                    image=(io.BytesIO(_png((7, 8, 9))), "photo.png"),
                    caption="Straight from the scope",
                ), content_type="multipart/form-data")
            assert response.status_code == 302
            assert response.headers["Location"].endswith(f"/markers/{key}")
        assert db["marker_definitions"].find_one({"Key": key}) is not None
        entry = db["marker_images"].find_one({"match.markerKeys": key})
        assert entry is not None
        assert entry["display"]["caption"] == "Straight from the scope"
    finally:
        _cleanup(key)


def test_creating_without_a_photo_is_unchanged(monkeypatch):
    app = _app(monkeypatch)
    key = "zznophoto"
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["username"] = "photo-test-user"
            token = _csrf(client)
            with patch("flymanager.app.routes.markers.enqueue_job"):
                response = client.post("/markers", data=_form(key, token),
                                       content_type="multipart/form-data")
            assert response.status_code == 302
        assert db["marker_definitions"].find_one({"Key": key}) is not None
        assert db["marker_images"].find_one({"match.markerKeys": key}) is None
    finally:
        _cleanup(key)


def test_an_empty_file_field_is_not_treated_as_a_photo(monkeypatch):
    """A browser posts the file input whether or not anything was chosen."""
    app = _app(monkeypatch)
    key = "zzemptyfile"
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["username"] = "photo-test-user"
            token = _csrf(client)
            with patch("flymanager.app.routes.markers.enqueue_job"):
                response = client.post("/markers", data=_form(
                    key, token, image=(io.BytesIO(b""), "")),
                    content_type="multipart/form-data")
            assert response.status_code == 302
            assert response.headers["Location"].endswith(f"/markers/{key}")
        assert db["marker_definitions"].find_one({"Key": key}) is not None
    finally:
        _cleanup(key)


def test_a_rejected_photo_still_leaves_the_marker_created(monkeypatch):
    app = _app(monkeypatch)
    key = "zzbadphoto"
    try:
        with app.test_client() as client:
            with client.session_transaction() as session:
                session["username"] = "photo-test-user"
            token = _csrf(client)
            with patch("flymanager.app.routes.markers.enqueue_job"):
                response = client.post("/markers", data=_form(
                    key, token,
                    image=(io.BytesIO(b"this is not an image"), "notes.txt"),
                ), content_type="multipart/form-data")
                assert response.status_code == 302
                assert response.headers["Location"].endswith(f"/markers/{key}")
                page = client.get(f"/markers/{key}").get_data(as_text=True)
        assert db["marker_definitions"].find_one({"Key": key}) is not None
        assert db["marker_images"].find_one({"match.markerKeys": key}) is None
        assert "photo" in page.lower()
    finally:
        _cleanup(key)


def test_the_create_form_can_carry_a_file_at_all(monkeypatch):
    app = _app(monkeypatch)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "photo-test-user"
        page = client.get("/markers/new").get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in page
    assert 'name="image"' in page
    assert 'name="caption"' in page
