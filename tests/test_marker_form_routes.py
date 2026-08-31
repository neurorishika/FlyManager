"""The marker catalog pages as a lab user sees them.

The catalog UI is the only place a non-developer changes prediction behaviour,
so these tests pin the two properties that make it usable: an ordinary user
gets real form fields (never a JSON textarea), and a save through those fields
produces the same envelope the JSON editor produced -- including the sections
the form never shows.
"""
from unittest.mock import patch

import pytest

from tests.test_marker_routes import (app, fake_db, _client, _reset_catalog,  # noqa: F401
                                      _settings_payload)
from flymanager.utils.mongo.marker_definitions import get_marker_definition


def _create(client, **overrides):
    data = {
        "Key": "zz", "kind": "gene_marker",
        "match.symbol": "zz",
        "payload.display_label": "zz",
        "payload.effect": "zigzag wings",
        "payload.body_part": "wing",
        "payload.chromosome": "3",
        "payload.dominance": "dominant",
        "payload.scoring_confidence": "0.8",
        "imaging.aliases": "zz, zigzag",
        "provenance.source": "user",
    }
    data.update(overrides)
    return client.post("/markers", data=data)


def test_a_structured_create_stores_the_same_envelope_as_the_json_editor(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _create(_client(app, fake_db))
        stored = get_marker_definition(fake_db, "zz")
    assert response.status_code == 302
    assert stored["kind"] == "gene_marker"
    assert stored["match"] == {"symbol": "zz"}
    assert stored["payload"]["chromosome"] == 3
    assert stored["payload"]["scoring_confidence"] == 0.8
    assert stored["payload"]["effect"] == "zigzag wings"
    assert stored["imaging"]["aliases"] == ["zz", "zigzag"]
    assert stored["provenance"] == {"source": "user"}


def test_a_structured_edit_does_not_wipe_sections_the_form_never_shows(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db)
        _create(client)
        fake_db.marker_definitions.update_one(
            {"Key": "zz"}, {"$set": {"sorting": {"stability": 0.4}}})
        client.post("/markers/zz", data={
            "kind": "gene_marker", "match.symbol": "zz",
            "payload.display_label": "zigzag", "imaging.aliases": "zz",
        })
        stored = get_marker_definition(fake_db, "zz")
    assert stored["sorting"] == {"stability": 0.4}
    assert stored["payload"]["display_label"] == "zigzag"
    # A field the form rendered and the user cleared really is cleared.
    assert "effect" not in stored["payload"]


def test_a_bad_number_in_a_form_field_flashes_instead_of_500ing(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _create(_client(app, fake_db), **{"payload.chromosome": "banana"})
    assert response.status_code == 302
    assert get_marker_definition(fake_db, "zz") is None


def test_a_normal_user_sees_form_fields_and_no_raw_json(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db)
        _create(client)
        body = client.get("/markers/zz").data.decode()
    assert 'name="payload.effect"' in body
    assert 'name="payload"' not in body
    assert "Raw definition" not in body


def test_an_admin_still_gets_the_raw_json_editor(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        _create(_client(app, fake_db, "admin"))
        body = _client(app, fake_db, "admin").get("/markers/zz").data.decode()
    assert "Raw definition" in body
    assert 'name="payload"' in body


@pytest.mark.parametrize("kind", ["gene_marker", "allele_marker", "alias",
                                  "balancer", "construct_marker"])
def test_every_kind_renders_its_own_fields_on_the_create_page(app, fake_db, kind):
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/new?kind=" + kind).data.decode()
    assert 'data-marker-kind="%s"' % kind in body


def test_the_catalog_page_speaks_english_not_schema(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers").data.decode()
    # "overlay" and a bare "JSON" are deliberately absent: they appear in the
    # shared layout as a CSS class and a JS builtin, not as page copy.
    for jargon in ("Mongo", "envelope", "raw JSON", "payload\"", "provenance"):
        assert jargon not in body
