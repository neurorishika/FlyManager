import re
from datetime import datetime, timedelta
from unittest.mock import ANY, MagicMock, patch

import pytest

from flymanager.app import create_app


def _settings_payload():
    return {
        "lab_info": {
            "lab_name": "Test Lab",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
        "theme": {
            "accent_color": "#0055aa",
            "dark_mode": False,
        },
    }


def _make_app(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        app = create_app()
    app.config.update(TESTING=True)
    return app


def _extract_csrf_token(response_text):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


def _get_authenticated_csrf_token(client):
    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        response = client.get("/flip/")
    return _extract_csrf_token(response.get_data(as_text=True))


def _stock_metadata_lookup(metadata_type, _db):
    metadata = {
        "types": ["Balancer"],
        "food_types": ["Molasses"],
        "provenances": ["Internal"],
        "genesX": [],
        "genes2nd": [],
        "genes3rd": [],
        "genes4th": [],
        "species": ["D. melanogaster"],
    }
    return metadata[metadata_type]


def _get_stock_creation_csrf_token(client):
    with patch(
        "flymanager.app.routes.stock.get_metadata",
        side_effect=_stock_metadata_lookup,
    ):
        response = client.get("/stock/add")
    return _extract_csrf_token(response.get_data(as_text=True))


def _cross_metadata_lookup(metadata_type, _db):
    metadata = {
        "food_types": ["Molasses"],
    }
    return metadata[metadata_type]


def _get_cross_creation_csrf_token(client):
    with patch("flymanager.app.routes.cross.get_available_ports", return_value=[]), patch(
        "flymanager.app.routes.cross.get_metadata",
        side_effect=_cross_metadata_lookup,
    ), patch("flymanager.app.routes.cross.get_all_genotypes", return_value=[]):
        response = client.get("/cross/add_cross")
    return _extract_csrf_token(response.get_data(as_text=True))


def test_login_requires_csrf(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        response = client.post(
            "/auth/login",
            data={"username": "user", "password": "bad-password"},
        )

    assert response.status_code == 400


def test_login_post_accepts_valid_csrf(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        get_response = client.get("/auth/login")
        csrf_token = _extract_csrf_token(get_response.get_data(as_text=True))

        with patch(
            "flymanager.app.routes.auth.verify_user_password",
            return_value=(False, False),
        ):
            response = client.post(
                "/auth/login",
                data={
                    "username": "user",
                    "password": "bad-password",
                    "csrf_token": csrf_token,
                },
            )

    assert response.status_code == 200
    assert b"Invalid username or password." in response.data


def test_update_theme_requires_csrf(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        response = client.post("/main/update_theme", json={"theme": "dark"})

    assert response.status_code == 400


def test_update_theme_accepts_valid_csrf(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        csrf_token = _get_authenticated_csrf_token(client)
        response = client.post(
            "/main/update_theme",
            json={"theme": "dark"},
            headers={"X-CSRFToken": csrf_token},
        )

    assert response.status_code == 200
    assert response.get_json() == {"status": "success"}


def test_stock_creation_requires_explicit_confirmation(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        csrf_token = _get_stock_creation_csrf_token(client)

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch("flymanager.app.routes.stock.add_to_stock") as add_to_stock:
            response = client.post(
                "/stock/add",
                data={"csrf_token": csrf_token},
            )

    assert response.status_code == 200
    assert b"Please confirm the stock creation before submitting." in response.data
    add_to_stock.assert_not_called()


def test_cross_creation_requires_explicit_confirmation(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        csrf_token = _get_cross_creation_csrf_token(client)

        with patch("flymanager.app.routes.cross.get_available_ports", return_value=[]), patch(
            "flymanager.app.routes.cross.get_metadata",
            side_effect=_cross_metadata_lookup,
        ), patch("flymanager.app.routes.cross.get_all_genotypes", return_value=[]), patch(
            "flymanager.app.routes.cross.add_to_cross"
        ) as add_to_cross:
            response = client.post(
                "/cross/add_cross",
                data={"csrf_token": csrf_token},
            )

    assert response.status_code == 200
    assert b"Please confirm the cross creation before submitting." in response.data
    add_to_cross.assert_not_called()


def test_csp_header_uses_nonce_without_unsafe_inline_scripts(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/auth/login")

    csp_header = response.headers["Content-Security-Policy"]
    assert "script-src 'self' 'nonce-" in csp_header
    assert "style-src 'self' 'nonce-" in csp_header
    assert "style-src-elem 'self' 'nonce-" in csp_header
    assert "'unsafe-inline'" not in csp_header


def test_home_page_renders_without_inline_style_attributes(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.main.get_user_stocks", return_value=[]), patch(
            "flymanager.app.routes.main.get_user_crosses", return_value=[]
        ), patch("flymanager.app.routes.main.get_user_trays", return_value=[]), patch(
            "flymanager.app.routes.main.get_user_activities", return_value=[]
        ), patch("flymanager.app.routes.main.get_flip_schedule", return_value={}):
            response = client.get("/home")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "style=" not in page


def test_home_page_renders_dashboard_command_sections(monkeypatch):
    app = _make_app(monkeypatch)
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    stocks = [
        {
            "Status": "Healthy",
            "Name": "Stock Alpha",
            "UniqueID": "STK001",
            "TrayID": "T1",
            "TrayPosition": "1",
            "CurrentlyAliveVials": "1,2,3",
            "NextFlipDates": yesterday,
        }
    ]
    crosses = [
        {
            "Status": "Showing Issues",
            "Name": "Cross Beta",
            "UniqueID": "CRS001",
            "TrayID": "T1",
            "TrayPosition": "2",
            "CurrentlyAliveVials": "1,2",
            "NextFlipDates": tomorrow,
        }
    ]
    trays = [{"TrayID": "T1", "Rows": 2, "Columns": 3, "Name": "Incubator A"}]
    activities = [
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "activity": "Added cross CRS001",
        },
        {
            "timestamp": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "activity": "Downloaded data to Excel",
        },
    ]
    schedule = {
        today: ['<a href="/flip/">Flip Stock Alpha</a>'],
        tomorrow: ['<a href="/cross/CRS001">Check Cross Beta</a>'],
    }
    occupancy = {
        "1": {
            "type": "stock",
            "id": "STK001",
            "name": "Stock Alpha",
            "status": "Healthy",
            "display_name": "Stock Alpha (STK001)",
        },
        "2": {
            "type": "cross",
            "id": "CRS001",
            "name": "Cross Beta",
            "status": "Showing Issues",
            "display_name": "Cross Beta (CRS001)",
        },
        "11": {
            "type": "blocked",
            "blocked_by": "1",
            "blocked_by_type": "stock",
            "blocked_by_name": "Stock Alpha (STK001)",
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.main.get_user_stocks", return_value=stocks), patch(
            "flymanager.app.routes.main.get_user_crosses", return_value=crosses
        ), patch("flymanager.app.routes.main.get_user_trays", return_value=trays), patch(
            "flymanager.app.routes.main.get_user_activities", return_value=activities
        ), patch("flymanager.app.routes.main.get_flip_schedule", return_value=schedule), patch(
            "flymanager.app.routes.main.get_tray_occupancy", return_value=occupancy
        ):
            response = client.get("/home")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Tray Heatmap" in page
    assert "My Queue" in page
    assert "Trend Signals" in page
    assert "Overdue Drift" in page
    assert "Tray Heatmap" in page
    assert ">Open<" in page


def test_login_page_uses_local_vendor_assets(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/auth/login")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "/static/vendor/bootstrap/bootstrap-4.5.2.min.css" in page
    assert "/static/vendor/fontawesome/css/all.min.css" in page
    assert "/static/vendor/jquery/jquery-3.5.1.min.js" in page
    assert "/static/vendor/popper/popper-1.16.1.min.js" in page
    assert "/static/vendor/bootstrap/bootstrap-4.5.2.min.js" in page
    assert "https://stackpath.bootstrapcdn.com/bootstrap/4.5.2" not in page
    assert "https://code.jquery.com/jquery-3.5.1.min.js" not in page
    assert "https://cdn.jsdelivr.net/npm/popper.js@1.16.1/dist/umd/popper.min.js" not in page
    assert "https://fonts.googleapis.com" not in page
    assert "https://cdnjs.cloudflare.com/ajax/libs/font-awesome" not in page


def test_permissions_policy_blocks_camera_when_feature_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CAMERA_SCANNER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/auth/login")

    assert response.status_code == 200
    assert "camera=()" in response.headers["Permissions-Policy"]


def test_permissions_policy_allows_camera_when_feature_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CAMERA_SCANNER", "1")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/auth/login")

    assert response.status_code == 200
    assert "camera=(self)" in response.headers["Permissions-Policy"]


def test_flip_page_hides_webserial_option_when_feature_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CLIENT_SERIAL_SCANNER", "0")
    app = _make_app(monkeypatch)

    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            response = client.get("/flip/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Client Serial Scanner (Chrome or Edge)" not in page


def test_flip_page_shows_webserial_option_when_feature_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CLIENT_SERIAL_SCANNER", "1")
    app = _make_app(monkeypatch)

    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            response = client.get("/flip/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Client Serial Scanner (Chrome or Edge)" in page


def test_flip_page_hides_camera_option_when_feature_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CAMERA_SCANNER", "0")
    app = _make_app(monkeypatch)

    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            response = client.get("/flip/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Camera Scanner" not in page


def test_flip_page_shows_camera_option_when_feature_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_CAMERA_SCANNER", "1")
    app = _make_app(monkeypatch)

    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            response = client.get("/flip/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Camera Scanner" in page


def test_reminder_route_rejects_get(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        response = client.get("/test_send_reminder")

    assert response.status_code == 405


def test_reminder_route_is_admin_only(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "scientist"

        csrf_token = _get_authenticated_csrf_token(client)
        response = client.post(
            "/test_send_reminder",
            headers={"X-CSRFToken": csrf_token},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_reminder_route_allows_admin_post(monkeypatch):
    app = _make_app(monkeypatch)

    with patch("flymanager.app.routes.main.schedule_daily_flip_reminders") as schedule_mock:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "admin"

            csrf_token = _get_authenticated_csrf_token(client)
            response = client.post(
                "/test_send_reminder",
                headers={"X-CSRFToken": csrf_token},
            )

    assert response.status_code == 200
    assert response.get_json() == {"status": "success"}
    schedule_mock.assert_called_once()


def test_stock_uid_lookup_is_limited_to_current_user(monkeypatch):
    app = _make_app(monkeypatch)
    collection = MagicMock()
    collection.find_one.return_value = None

    with patch("flymanager.app.routes.stock.db", {"stocks": collection}):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            response = client.get("/stock/get_stock_data_for_uid/ABC123")

    assert response.status_code == 404
    collection.find_one.assert_called_once_with(
        {"UniqueID": "ABC123", "User": "scientist"}
    )


def test_flip_uid_lookup_returns_shared_scan_payload(monkeypatch):
    app = _make_app(monkeypatch)
    payload = {
        "uniqueID": "ABC123",
        "name": "Stock A",
        "genotype": "w1118",
        "status": "Healthy",
    }

    with patch(
        "flymanager.app.routes.flip.scanner_service.lookup_uid_result",
        return_value=("stock", payload),
    ) as lookup_mock:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            csrf_token = _get_authenticated_csrf_token(client)
            response = client.post(
                "/flip/lookup_uid",
                json={"uniqueID": "ABC123"},
                headers={"X-CSRFToken": csrf_token},
            )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "itemType": "stock",
        "payload": payload,
    }
    lookup_mock.assert_called_once_with("scientist", "ABC123", ANY)


def test_flip_uid_lookup_returns_404_for_unknown_uid(monkeypatch):
    app = _make_app(monkeypatch)

    with patch(
        "flymanager.app.routes.flip.scanner_service.lookup_uid_result",
        return_value=(None, None),
    ):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["username"] = "scientist"

            csrf_token = _get_authenticated_csrf_token(client)
            response = client.post(
                "/flip/lookup_uid",
                json={"uniqueID": "MISSING"},
                headers={"X-CSRFToken": csrf_token},
            )

    assert response.status_code == 404
    assert response.get_json() == {
        "success": False,
        "message": "UID not recognized for this user.",
        "uniqueID": "MISSING",
    }


def test_data_download_is_admin_only(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "scientist"

        response = client.get("/data/download")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_public_deploy_requires_secret_key(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.setenv("FLYMANAGER_DOMAIN", "flymanager.example.com")
    monkeypatch.delenv("SECRET_KEY", raising=False)

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        with pytest.raises(RuntimeError, match="SECRET_KEY must be explicitly set"):
            create_app()