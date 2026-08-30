import io
import re
from unittest.mock import patch

from PIL import Image

from flymanager.app import create_app, db
from flymanager.utils.phenotypes.image_catalog import read_image_revision
from flymanager.utils.phenotypes.marker_catalog import get_catalog


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


def test_upload_serve_delete_and_signature_isolation(monkeypatch):
    app = _app(monkeypatch)
    raw = _png((31, 32, 33))
    before_signature = get_catalog()["signature"]
    before_revision = read_image_revision(db)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "image-test-user"
        token = _csrf(client)
        response = client.post(
            "/markers/Sb/images",
            data={"image": (io.BytesIO(raw), "photo.png"), "caption": "Test image",
                  "csrf_token": token}, content_type="multipart/form-data")
        assert response.status_code == 302
        entry = db["marker_images"].find_one({"UploadedBy": "image-test-user"})
        assert entry and entry["match"]["markerKeys"] == ["Sb"]
        detail = client.get("/markers/Sb")
        assert f"/markers/images/{entry['imageId']}".encode() in detail.data
        assert b'Test image' in detail.data
        assert b'name="image"' in detail.data
        served = client.get(f"/markers/images/{entry['imageId']}")
        assert served.status_code == 200
        assert served.headers["ETag"].strip('"') == entry["sha256"]
        assert "immutable" in served.headers["Cache-Control"]
        deleted = client.post(f"/markers/images/{entry['imageId']}/delete",
                              data={"csrf_token": token, "marker_key": "Sb"})
        assert deleted.status_code == 302
    assert db["marker_images"].find_one({"imageId": entry["imageId"]}) is None
    assert read_image_revision(db) >= before_revision + 2
    assert get_catalog()["signature"] == before_signature


def test_image_routes_require_login_and_validate_marker(monkeypatch):
    app = _app(monkeypatch)
    with app.test_client() as client:
        assert client.get("/markers/images/img_missing").status_code == 302
        with client.session_transaction() as session:
            session["username"] = "image-test-user"
        token = _csrf(client)
        response = client.post(
            "/markers/NotAMarker/images",
            data={"image": (io.BytesIO(_png()), "photo.png"), "csrf_token": token},
            content_type="multipart/form-data")
        assert response.status_code == 404


def test_non_admin_cannot_delete_shipped_image(monkeypatch):
    app = _app(monkeypatch)
    shipped = db["marker_images"].find_one({"origin": "shipped"})
    assert shipped
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "image-test-user"
        response = client.post(
            f"/markers/images/{shipped['imageId']}/delete",
            data={"csrf_token": _csrf(client)})
        assert response.status_code == 403
    assert db["marker_images"].find_one({"imageId": shipped["imageId"]})
