"""The raw envelope editor is admin-only on the server, not just in the UI.

The marker pages render structured fields for everyone and the raw
per-section JSON editor only for an admin. But `_parse_form_document` chooses
its raw-envelope branch purely on the SHAPE of the POST, so a hand-built form
post carrying `match`/`audit`/`provenance` bypassed the UI gate entirely.

That matters because catalog writes enqueue a lab-wide prediction-cache
rebuild: a non-admin could push hand-crafted match rules that change what
every user's genotypes resolve to. The UI gate was decorative; this pins the
server-side one.
"""
import json
from unittest.mock import patch

import pytest

from tests.test_marker_routes import (app, fake_db, _client, _reset_catalog,  # noqa: F401
                                      _settings_payload)
from flymanager.utils.mongo.marker_definitions import get_marker_definition


def _raw_form(key="zz"):
    """A raw post carrying the privileged sections.

    match/payload/imaging/provenance are deliberately NOT gated -- they are the
    marker's own content, reachable through the structured form's dotted fields
    by any user who may edit the row, and the JSON API must keep accepting them.
    What is gated is sorting (feeds the crossing constraint solver), audit
    (FlyBase probe symbols) and expression (reserved).
    """
    return {"Key": key, "kind": "gene_marker",
            "match": json.dumps({"symbol": key}),
            "payload": json.dumps({"display_label": key}),
            "audit": json.dumps({"isProbeMarker": True, "probeSymbol": key}),
            "sorting": json.dumps({"stabilityScore": 0.99})}


def _structured_form(key="zz", label="zz"):
    return {"Key": key, "kind": "gene_marker", "match.symbol": key,
            "payload.display_label": label, "provenance.source": "user"}


def test_a_non_admin_cannot_post_raw_envelope_json(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _client(app, fake_db, "mallory").post("/markers", data=_raw_form())
    assert response.status_code in (302, 403)
    assert get_marker_definition(fake_db, "zz") is None


def test_a_non_admin_cannot_smuggle_raw_json_through_an_update(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db, "mallory")
        client.post("/markers", data=_structured_form())
        client.post("/markers/zz", data={"kind": "gene_marker",
                                         "audit": json.dumps({"isProbeMarker": True})})
        stored = get_marker_definition(fake_db, "zz")
    assert stored is not None
    assert stored.get("audit") in ({}, None)


def test_the_json_api_is_gated_the_same_way(app, fake_db):
    """The API path is the same authority question as the form path."""
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _client(app, fake_db, "mallory").post("/markers", json={
            "Key": "api", "kind": "gene_marker",
            "match": {"symbol": "api"}, "payload": {"display_label": "api"},
            "audit": {"isProbeMarker": True}})  # audit is privileged
    assert response.status_code == 403
    assert get_marker_definition(fake_db, "api") is None


def test_an_admin_still_gets_the_raw_editor(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _client(app, fake_db, "admin").post("/markers", data=_raw_form("adminkey"))
        stored = get_marker_definition(fake_db, "adminkey")
    assert response.status_code == 302
    assert stored["audit"] == {"isProbeMarker": True, "probeSymbol": "adminkey"}
    assert stored["sorting"] == {"stabilityScore": 0.99}


def test_a_non_admin_can_still_create_a_marker_through_the_json_api(app, fake_db):
    """The gate must not break the ordinary API contract: a user creating a
    marker has to be able to send its match and payload."""
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        response = _client(app, fake_db, "alice").post("/markers", json={
            "Key": "ok", "kind": "gene_marker", "match": {"symbol": "ok"},
            "payload": {"display_label": "ok"}, "provenance": {"source": "user"}})
    assert response.status_code == 201
    assert get_marker_definition(fake_db, "ok")["match"] == {"symbol": "ok"}


def test_a_non_admin_can_still_use_the_structured_form(app, fake_db):
    """The gate must not cost an ordinary user their normal editing path."""
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db, "alice")
        response = client.post("/markers", data=_structured_form(label="stubble"))
        stored = get_marker_definition(fake_db, "zz")
    assert response.status_code == 302
    assert stored["payload"]["display_label"] == "stubble"
    assert stored["match"] == {"symbol": "zz"}
