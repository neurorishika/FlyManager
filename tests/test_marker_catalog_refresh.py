import pytest

from flymanager.utils.phenotypes import marker_catalog, visual_markers
from tests.mongo_fakes import FakeDatabase

USER_MARKER = {
    "Key": "zz",
    "kind": "gene_marker",
    "match": {"symbol": "zz"},
    "payload": {"body_part": "wing", "effect": "zigzag wings",
                "dominance": "dominant", "display_label": "zz",
                "phenotype_key": "zz", "chromosome": 3,
                "scoring_confidence": 0.8},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {"source": "user"}, "origin": "user",
}


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _db(definitions=(), revision=0):
    return FakeDatabase({
        "marker_definitions": list(definitions),
        "settings": [{"markerCatalogRevision": revision}],
    })


def test_read_catalog_revision_defaults_to_zero():
    assert marker_catalog.read_catalog_revision(FakeDatabase({"settings": [{}]})) == 0


def test_refresh_folds_the_overlay_into_the_snapshot():
    snapshot = marker_catalog.refresh_catalog(_db([USER_MARKER], revision=1))
    assert snapshot["gene_markers"]["zz"]["effect"] == "zigzag wings"
    assert visual_markers.get_visual_marker("zz")["display_label"] == "zz"


def test_refresh_is_a_noop_when_the_revision_has_not_moved():
    db = _db([USER_MARKER], revision=1)
    first = marker_catalog.refresh_catalog(db)
    db["marker_definitions"].delete_many({})
    second = marker_catalog.refresh_catalog(db)
    assert second is first, "unchanged revision must not trigger a recompile"


def test_force_recompiles_even_at_the_same_revision():
    db = _db([USER_MARKER], revision=1)
    marker_catalog.refresh_catalog(db)
    db["marker_definitions"].delete_many({})
    snapshot = marker_catalog.refresh_catalog(db, force=True)
    assert "zz" not in snapshot["gene_markers"]


def test_a_revision_bump_from_another_process_is_picked_up():
    db = _db([], revision=1)
    marker_catalog.refresh_catalog(db)
    db["marker_definitions"].insert_one(dict(USER_MARKER))
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 2}})
    assert "zz" in marker_catalog.refresh_catalog(db)["gene_markers"]


def test_maybe_refresh_throttles_within_the_interval():
    db = _db([USER_MARKER], revision=1)
    assert marker_catalog.maybe_refresh_catalog(db, now=1000.0) is not None
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 2}})
    db["marker_definitions"].delete_many({})
    assert marker_catalog.maybe_refresh_catalog(db, now=1010.0) is None
    assert "zz" in marker_catalog.get_catalog()["gene_markers"]


def test_maybe_refresh_rechecks_after_the_interval():
    db = _db([USER_MARKER], revision=1)
    marker_catalog.maybe_refresh_catalog(db, now=1000.0)
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 2}})
    db["marker_definitions"].delete_many({})
    assert marker_catalog.maybe_refresh_catalog(db, now=1031.0) is not None
    assert "zz" not in marker_catalog.get_catalog()["gene_markers"]


def test_a_failed_recompile_leaves_the_previous_snapshot_installed():
    class Boom:
        def __getitem__(self, name):
            raise RuntimeError("mongo is down")

    marker_catalog.refresh_catalog(_db([USER_MARKER], revision=1))
    before = marker_catalog.get_catalog()
    with pytest.raises(RuntimeError):
        marker_catalog.refresh_catalog(Boom(), force=True)
    assert marker_catalog.get_catalog() is before


def test_an_invalid_overlay_row_is_skipped_not_fatal():
    snapshot = marker_catalog.refresh_catalog(
        _db([USER_MARKER, {"Key": "", "kind": "gene_marker"}], revision=1))
    assert "zz" in snapshot["gene_markers"]
    assert len(snapshot["invalid_definitions"]) == 1


def test_reading_the_revision_never_writes_to_settings():
    """The probe runs on every request; it must not create a settings document."""
    db = FakeDatabase({"marker_definitions": [], "settings": []})
    assert marker_catalog.read_catalog_revision(db) == 0
    assert db["settings"].count_documents({}) == 0


def test_a_document_with_bson_values_is_rejected_not_silently_altered():
    """A stray ObjectId in a payload is invalid data. Skipping it keeps the
    signature stable (so it cannot churn every materialized cache) and
    records it as invalid so the /markers UI can show it, rather than
    silently serving a marker that differs from what is stored."""
    from bson import ObjectId

    def build():
        db = _db([dict(USER_MARKER, _id=ObjectId(),
                       payload=dict(USER_MARKER["payload"], ref=ObjectId()))],
                 revision=1)
        marker_catalog.reset_catalog()
        marker_catalog.reset_refresh_state()
        return marker_catalog.refresh_catalog(db)

    first, second = build(), build()
    assert first["signature"] == second["signature"]
    assert "zz" not in first["gene_markers"]
    assert [entry["Key"] for entry in first["invalid_definitions"]] == ["zz"]


def test_a_clean_overlay_document_still_compiles_with_an_objectid_present():
    """Only the offending value is a problem -- a normal document that merely
    carries a Mongo _id must still compile."""
    from bson import ObjectId

    db = _db([dict(USER_MARKER, _id=ObjectId())], revision=1)
    snapshot = marker_catalog.refresh_catalog(db)
    assert snapshot["gene_markers"]["zz"]["effect"] == "zigzag wings"
    assert snapshot["invalid_definitions"] == []
