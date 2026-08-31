import json
from unittest.mock import patch

import pytest

from flymanager.app import create_app
from flymanager.utils.phenotypes import marker_catalog
from tests.mongo_fakes import FakeDatabase


def _settings_payload():
    return {
        "lab_info": {"lab_name": "Test Lab", "admin_name": "Admin",
                     "admin_email": "admin@example.com"},
        "theme": {"accent_color": "#0055aa", "dark_mode": False},
    }


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)
    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return application


@pytest.fixture
def fake_db():
    return FakeDatabase({"marker_definitions": [], "settings": [{}], "activity": []})


@pytest.fixture(autouse=True)
def _reset_catalog(monkeypatch):
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    # The Task 10 before_request hook closes over the real module-global `db`,
    # not the patched blueprint one, so without this it recompiles from the
    # real Mongo and discards whatever the test installed.
    monkeypatch.setattr(marker_catalog, "maybe_refresh_catalog",
                        lambda db, **kwargs: None)
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _client(app, fake_db, username="alice"):
    client = app.test_client()
    with client.session_transaction() as session:
        session["username"] = username
    return client


def _payload(key="zz"):
    return {
        "Key": key,
        "kind": "gene_marker",
        "match": {"symbol": key},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": key,
                    "phenotype_key": key, "chromosome": 3,
                    "scoring_confidence": 0.8},
        "provenance": {"source": "user"},
    }


def test_catalog_requires_login(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = app.test_client().get("/markers")
    assert response.status_code == 302


def test_catalog_lists_shipped_definitions(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers")
    body = response.data.decode()
    assert response.status_code == 200
    # A real row, not a status check: the Key, its origin label, and its
    # attribution must all be visible text, and nothing leaked as an
    # unrendered template expression.
    assert "CyO" in body
    assert "shipped" in body
    assert "FlyManager" in body
    assert "{{" not in body
    assert "Undefined" not in body


def test_detail_renders_a_shipped_definition_read_only(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers/Cy")
    body = response.data.decode()
    assert response.status_code == 200
    # A built-in marker offers a lab correction (an overlay) rather than an
    # in-place edit; the page says so in plain words now.
    assert "Save lab version" in body
    assert "curly wings" in body  # the effect actually rendered, not dumped
    assert "Built in" in body
    assert "{{" not in body
    assert "Undefined" not in body


def test_detail_404s_for_an_unknown_key(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        assert _client(app, fake_db).get("/markers/nope").status_code == 404


def test_create_enqueues_a_rebuild_and_refreshes_the_catalog(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job") as enqueue:
        response = _client(app, fake_db).post("/markers", json=_payload())
    assert response.status_code == 201
    assert fake_db["marker_definitions"].count_documents({"Key": "zz"}) == 1
    assert enqueue.call_count == 1
    assert enqueue.call_args.kwargs["kwargs"]["keys"] == ["zz"]
    assert marker_catalog.get_catalog()["gene_markers"]["zz"]["effect"] == "zigzag wings"


def test_creating_an_override_of_a_shipped_key_carries_previous_documents(app, fake_db):
    """create_marker is the override path for a shipped key (_require_editable
    409s any other in-place edit of one). The pre-edit state is the shipped
    row, which exists nowhere the rebuild scoping can see once the overlay
    row shadows it -- so it must be captured and threaded through exactly
    like update_marker/delete_marker already do, or a rebuild after
    overriding a shipped balancer's markers can never sweep the genotypes
    that depended on the old (shipped) marker set."""
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job") as enqueue:
        response = _client(app, fake_db).post("/markers", json=_payload("Cy"))
    assert response.status_code == 201
    previous_documents = enqueue.call_args.kwargs["kwargs"]["previous_documents"]
    assert previous_documents, "overriding a shipped key must carry its pre-edit state"
    assert previous_documents[0]["Key"] == "Cy"
    assert previous_documents[0]["origin"] == "shipped"


def test_creating_a_brand_new_marker_carries_no_previous_documents(app, fake_db):
    """A genuinely new key has no pre-edit state to sweep -- previous_documents
    must stay empty rather than the override path's fix accidentally
    fabricating one."""
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job") as enqueue:
        response = _client(app, fake_db).post("/markers", json=_payload("zz"))
    assert response.status_code == 201
    assert enqueue.call_args.kwargs["kwargs"]["previous_documents"] == []


def test_duplicate_create_returns_409(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db)
        client.post("/markers", json=_payload())
        assert client.post("/markers", json=_payload()).status_code == 409


def test_a_malformed_form_field_gets_a_real_redirect_not_a_stub(app, fake_db):
    """A non-JSON POST that fails to parse must get a genuine 3xx the browser
    will actually follow. redirect() paired with a non-3xx status makes
    Werkzeug send its raw "Redirecting..." stub body instead of redirecting,
    so a non-JS caller would see a blank stub rather than the flashed error
    on the page they land on."""
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _client(app, fake_db).post(
            "/markers",
            data={"Key": "zz", "kind": "gene_marker", "payload": "{not valid json"},
        )
    assert response.status_code == 302
    assert response.headers.get("Location")


def test_a_duplicate_form_create_gets_a_real_redirect_not_a_stub(app, fake_db):
    """Same as above, but through the MarkerDefinitionError -> _error_response
    path (a 409, not a 400) rather than the inline ValueError handler."""
    payload = _payload()
    form_payload = {
        "Key": payload["Key"],
        "kind": payload["kind"],
        "match": json.dumps(payload["match"]),
        "payload": json.dumps(payload["payload"]),
        "provenance": json.dumps(payload["provenance"]),
    }
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db)
        client.post("/markers", data=form_payload)
        response = client.post("/markers", data=form_payload)
    assert response.status_code == 302
    assert response.headers.get("Location")


def test_update_by_a_non_creator_returns_403(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        _client(app, fake_db, "alice").post("/markers", json=_payload())
        response = _client(app, fake_db, "bob").post("/markers/zz", json=_payload())
    assert response.status_code == 403


def test_delete_marks_the_deleted_override_for_a_full_rebuild(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job") as enqueue:
        client = _client(app, fake_db)
        client.post("/markers", json=_payload("Cy"))
        response = client.post("/markers/Cy/delete")
    assert response.status_code == 200
    assert enqueue.call_args.kwargs["kwargs"]["deleted_override_keys"] == ["Cy"]


def test_promote_requires_admin(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        _client(app, fake_db, "alice").post("/markers", json=_payload())
        # JSON caller: the status code is still meaningful on that branch.
        json_response = _client(app, fake_db, "alice").post(
            "/markers/zz/promote", json={})
        assert json_response.status_code == 403
        # Non-JSON (plain form) caller: a real 3xx redirect the browser will
        # follow, not redirect() paired with a 403 -- that pairing makes
        # Werkzeug send its raw "Redirecting..." stub instead of an actual
        # redirect, so a non-JS caller would never see the flashed message.
        form_response = _client(app, fake_db, "alice").post("/markers/zz/promote")
        assert form_response.status_code == 302
        assert _client(app, fake_db, "admin").post("/markers/zz/promote").status_code == 200


def test_a_queue_conflict_does_not_undo_the_write(app, fake_db):
    from flymanager.utils.mongo.operation_locks import OperationLockConflict

    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job",
               side_effect=OperationLockConflict("already running")):
        response = _client(app, fake_db).post("/markers", json=_payload())
    assert response.status_code == 201
    assert fake_db["marker_definitions"].count_documents({"Key": "zz"}) == 1


def test_catalog_renders_a_definition_with_no_payload_key(app, fake_db):
    # validate_definition does not require `payload` (it defaults a missing
    # one to {} with no error), and _normalized() setdefaults sorting/audit/
    # imaging/expression/provenance but not payload -- so a document that
    # omits `payload` entirely is a legitimate, storable row, not a malformed
    # one caught by invalid_definitions. The list template must not assume
    # every row has a payload key just because most do.
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        create_response = _client(app, fake_db).post(
            "/markers",
            json={"Key": "no-payload", "kind": "gene_marker", "match": {"symbol": "no-payload"}},
        )
        assert create_response.status_code == 201
        response = _client(app, fake_db).get("/markers")
    assert response.status_code == 200
    assert b"no-payload" in response.data


def test_invalid_overlay_rows_are_surfaced_in_the_catalog(app, fake_db):
    fake_db["marker_definitions"].insert_one({"Key": "", "kind": "gene_marker"})
    fake_db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 1}})
    marker_catalog.refresh_catalog(fake_db, force=True)
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers")
    assert b"Key is required" in response.data


def test_detail_shows_default_markers_for_a_balancer(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers/CyO")
    body = response.data.decode()
    assert response.status_code == 200
    # CyO's shipped payload.default_markers is ["Cy", "pr", "cn"]; a dumped
    # Python dict/list repr (e.g. "['Cy', 'pr', 'cn']") is not acceptable --
    # each symbol must appear as real page content.
    assert "Cy" in body
    assert "pr" in body
    assert "cn" in body
    assert "balancer" in body
    assert "[&#39;Cy&#39;" not in body
    assert "['Cy'" not in body
    assert "{{" not in body
    assert "Undefined" not in body
