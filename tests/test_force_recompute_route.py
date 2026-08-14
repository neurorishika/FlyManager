from unittest.mock import ANY, patch

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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def test_force_recompute_rejects_unknown_cache_key(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        response = client.post(
            "/settings/force-recompute-cache/not-a-real-cache",
            data={"confirmPhrase": "whatever"},
        )

    assert response.status_code == 404


def test_force_recompute_rejects_wrong_confirm_phrase(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.settings.enqueue_job") as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/phenotype",
                data={"confirmPhrase": "wrong phrase"},
            )

    assert response.status_code == 302
    enqueue_job.assert_not_called()


def test_force_recompute_rejects_when_cooldown_active(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.check_force_refresh_cooldown",
            return_value=(False, "2026-08-15 10:00"),
        ) as cooldown_check, patch(
            "flymanager.app.routes.settings.enqueue_job"
        ) as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/phenotype",
                data={"confirmPhrase": "FORCE RECOMPUTE PHENOTYPE"},
            )

    assert response.status_code == 302
    cooldown_check.assert_called_once_with("phenotype", ANY)
    enqueue_job.assert_not_called()


def test_force_recompute_dispatches_job_when_confirmed_and_cooldown_clear(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.check_force_refresh_cooldown",
            return_value=(True, None),
        ), patch(
            "flymanager.app.routes.settings.enqueue_job",
            return_value="job-key-123",
        ) as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/provider_match",
                data={"confirmPhrase": "FORCE RECOMPUTE PROVIDER MATCH"},
            )

    assert response.status_code == 302
    enqueue_job.assert_called_once()
    call_kwargs = enqueue_job.call_args.kwargs
    assert call_kwargs["key"] == "maintenance:force-cache-refresh:provider_match"
    assert call_kwargs["kwargs"] == {
        "key": "maintenance:force-cache-refresh:provider_match",
        "username": "admin",
        "cache_key": "provider_match",
    }


def test_force_recompute_requires_admin_session(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "not-admin"

        with patch("flymanager.app.routes.settings.enqueue_job") as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/phenotype",
                data={"confirmPhrase": "FORCE RECOMPUTE PHENOTYPE"},
            )

    assert response.status_code == 302
    enqueue_job.assert_not_called()
