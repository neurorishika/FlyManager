import pytest

from flymanager.utils.constraints.balancer_selection import _candidate_documents
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.data.balancer_ingest import \
    ingest_balancer_definitions
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _report():
    return {
        "definitions": {
            "source_url": "https://bdsc.example/balancers",
            "balancers": [
                {"symbol": "ZZ7", "chromosome": 3, "family": "ZZ7",
                 "default_markers": ["Sb"], "notes": ["From BDSC."]},
            ],
        }
    }


def test_ingest_still_populates_the_staging_collection():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    summary = ingest_balancer_definitions(db, _report())
    assert summary["inserted"] == 1
    assert db["balancer_definitions"].count_documents({"symbol": "ZZ7"}) == 1


def test_ingest_writes_a_curated_catalog_row():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())

    row = db["marker_definitions"].find_one({"Key": "ZZ7"})
    assert row["kind"] == "balancer"
    assert row["origin"] == "curated"
    assert row["payload"]["default_markers"] == ["Sb"]
    assert row["payload"]["chromosome"] == 3
    assert marker_catalog.read_catalog_revision(db) == 1


def test_the_ingested_balancer_resolves_through_the_catalog():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())
    marker_catalog.refresh_catalog(db, force=True)

    assert "ZZ7" in marker_catalog.get_catalog()["known_balancer_symbols"]
    candidates = _candidate_documents(3, db)
    assert "ZZ7" in {candidate.get("symbol") for candidate in candidates}


def test_re_ingesting_does_not_duplicate_catalog_rows():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())
    ingest_balancer_definitions(db, _report())
    assert db["marker_definitions"].count_documents({"Key": "ZZ7"}) == 1


def test_a_user_override_of_an_ingested_balancer_is_not_clobbered():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())
    db["marker_definitions"].update_one(
        {"Key": "ZZ7"},
        {"$set": {"origin": "user", "CreatedBy": "alice",
                  "payload": {"family": "ZZ7", "chromosome": 3,
                              "default_markers": ["Cy"], "notes": []}}})

    ingest_balancer_definitions(db, _report())

    row = db["marker_definitions"].find_one({"Key": "ZZ7"})
    assert row["origin"] == "user"
    assert row["payload"]["default_markers"] == ["Cy"]


def test_breakpoint_data_survives_the_move_to_the_catalog():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    report = _report()
    report["definitions"]["balancers"][0]["breakpoint_regions"] = ["61A", "89E"]
    report["definitions"]["balancers"][0]["breakpoint_text"] = "In(3LR)61A;89E"
    ingest_balancer_definitions(db, report)
    marker_catalog.refresh_catalog(db, force=True)

    candidate = next(c for c in _candidate_documents(3, db) if c["symbol"] == "ZZ7")
    assert candidate["breakpoint_regions"] == ["61A", "89E"]
    assert candidate["breakpoint_text"] == "In(3LR)61A;89E"


def test_the_staging_collection_is_no_longer_read():
    """A row present only in balancer_definitions must not reach scoring."""
    db = FakeDatabase({
        "balancer_definitions": [{"symbol": "STALE", "chromosome": 3}],
        "marker_definitions": [], "settings": [{}]})
    marker_catalog.refresh_catalog(db, force=True)
    assert "STALE" not in {c.get("symbol") for c in _candidate_documents(3, db)}
