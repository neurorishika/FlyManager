"""The create page checks a Key while it is typed, not at submit.

A collision today is a 409 flash after the person has filled in the whole
form. Worse, two of the three outcomes are not collisions at all: reusing a
SHIPPED key is the supported way to keep a lab's own version of a built-in
marker, and saying "duplicate" there would talk someone out of doing the
right thing.
"""
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
    monkeypatch.setattr(marker_catalog, "maybe_refresh_catalog",
                        lambda db, **kwargs: None)
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _client(app, username="alice"):
    client = app.test_client()
    with client.session_transaction() as session:
        session["username"] = username
    return client


def _check(app, fake_db, key):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app).get("/markers/key-check", query_string={"key": key})
    assert response.status_code == 200
    return response.get_json()


def test_it_requires_login(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = app.test_client().get("/markers/key-check?key=zz")
    assert response.status_code in (302, 401)


def test_an_unused_key_is_free(app, fake_db):
    assert _check(app, fake_db, "zzfree")["status"] == "free"


def test_a_shipped_key_is_an_override_not_a_duplicate(app, fake_db):
    result = _check(app, fake_db, "Sb")
    assert result["status"] == "shipped"
    assert "override" in result["message"].lower()


def test_a_lab_marker_is_taken_and_linked(app, fake_db):
    fake_db["marker_definitions"].insert_one(
        {"Key": "zztaken", "kind": "gene_marker", "origin": "user"})
    result = _check(app, fake_db, "zztaken")
    assert result["status"] == "taken"
    assert result["url"].endswith("/markers/zztaken")


def test_an_overlay_row_is_seen_even_when_the_snapshot_is_stale(app, fake_db):
    """The in-process catalog can be a refresh interval behind, so a marker
    another worker created seconds ago would otherwise read as free."""
    marker_catalog.set_catalog(
        marker_catalog.compile_catalog(marker_catalog.load_shipped_catalog(), []))
    fake_db["marker_definitions"].insert_one(
        {"Key": "zzfresh", "kind": "gene_marker", "origin": "user"})
    assert "zzfresh" not in marker_catalog.get_catalog()["definitions"]
    assert _check(app, fake_db, "zzfresh")["status"] == "taken"


def test_whitespace_is_stripped_the_way_the_create_path_strips_it(app, fake_db):
    fake_db["marker_definitions"].insert_one(
        {"Key": "zzspace", "kind": "gene_marker", "origin": "user"})
    assert _check(app, fake_db, "  zzspace  ")["status"] == "taken"


def test_the_check_is_case_sensitive_because_the_store_is(app, fake_db):
    """create_marker_definition matches the key exactly, so reporting "SB" as
    taken because "Sb" exists would be a lie in the safe direction."""
    assert _check(app, fake_db, "sb")["status"] == "free"


def test_a_blank_key_says_nothing_rather_than_free(app, fake_db):
    assert _check(app, fake_db, "  ")["status"] == "empty"


def test_the_create_page_renders_the_constrained_controls(app, fake_db):
    """One render-level check that the vocabularies actually reach the page:
    a datalist that never renders is a typo guard nobody gets."""
    with patch("flymanager.app.routes.markers.db", fake_db):
        page = _client(app).get("/markers/new").get_data(as_text=True)
    assert 'list="f-gene_marker-payload.body_part-options"' in page
    assert "<datalist" in page
    assert 'data-marker-tags' in page
    # |capitalize would lowercase the rest and render this as "1 (x)".
    assert "1 (X)" in page


def test_a_stored_value_outside_the_options_is_offered_back(app, fake_db):
    """A select posts what it shows. A chromosome of 5, set through the admin
    JSON editor, would otherwise be silently cleared by opening and saving."""
    fake_db["marker_definitions"].insert_one({
        "Key": "zzodd", "kind": "gene_marker", "origin": "user",
        "CreatedBy": "alice",
        "match": {"symbol": "zzodd"}, "payload": {"chromosome": 5},
    })
    with patch("flymanager.app.routes.markers.db", fake_db):
        page = _client(app).get("/markers/zzodd").get_data(as_text=True)
    assert '<option value="5" selected>5</option>' in page
