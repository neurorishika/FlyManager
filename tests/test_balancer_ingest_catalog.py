import pytest

from flymanager.utils.constraints.balancer_selection import _candidate_documents
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.data.balancer_ingest import (
    ingest_balancer_definitions, parse_bdsc_balancer_definitions_html)
from flymanager.utils.phenotypes.predictor import predict_stock_phenotype
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
            # _normalize_chromosome (the real BDSC parser) emits strings
            # ("X", "1".."4"), never ints. Seeding an int here would hide a
            # chromosome-type mismatch between the catalog row and the int
            # normalize_chromosome_label produces for candidate matching.
            #
            # This must also match parse_bdsc_balancer_definitions_html's
            # REAL output shape: it emits `marker_tokens` (never
            # `default_markers`/`aliases`/`family`), and `notes` as a single
            # STRING (never a list). A fixture shaped any other way would
            # exercise a code path production never hits.
            "balancers": [
                {"symbol": "ZZ7", "chromosome": "3",
                 "marker_tokens": ["Sb"], "notes": "From BDSC."},
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


def test_a_string_chromosome_from_the_real_parser_still_reaches_scoring():
    """_normalize_chromosome emits strings ("2"), but candidate matching
    compares against the int from normalize_chromosome_label. Storing the
    raw string silently drops every ingested balancer from scoring."""
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    report = _report()
    report["definitions"]["balancers"][0]["chromosome"] = "3"
    ingest_balancer_definitions(db, report)
    marker_catalog.refresh_catalog(db, force=True)

    assert "ZZ7" in {c.get("symbol") for c in _candidate_documents(3, db)}


def test_re_ingesting_a_shipped_balancer_does_not_drop_it_from_candidates():
    """Re-ingest must not clobber a working shipped row with an
    incompatible chromosome type, and (per the shipped-content-preserving
    merge in _upsert_catalog_balancers) must not clobber its curated marker
    set with a re-scrape either."""
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    marker_catalog.refresh_catalog(db, force=True)
    assert "CyO" in {c.get("symbol") for c in _candidate_documents(2, db)}

    report = _report()
    report["definitions"]["balancers"][0] = {
        "symbol": "CyO", "chromosome": "2",
        "marker_tokens": ["Cy"], "notes": "",
    }
    ingest_balancer_definitions(db, report)
    marker_catalog.refresh_catalog(db, force=True)

    assert "CyO" in {c.get("symbol") for c in _candidate_documents(2, db)}
    row = db["marker_definitions"].find_one({"Key": "CyO"})
    # The shipped payload -- not the (deliberately impoverished, here) scraped
    # one -- must survive the re-ingest.
    assert row["payload"]["default_markers"] == ["Cy", "pr", "cn"]


def test_ingesting_the_real_parsers_output_preserves_a_shipped_balancers_markers():
    """End-to-end regression for the real bug: _catalog_row_from_balancer used
    to read document["default_markers"]/["aliases"]/["family"] and treat
    document["notes"] as a list, none of which the REAL parser
    (parse_bdsc_balancer_definitions_html) emits -- it emits `marker_tokens`
    and a string `notes`. Every ingested row therefore got
    default_markers: [], and because the overlay wins over the shipped by
    Key, re-scraping CyO replaced its working shipped definition with a
    marker-less one. This feeds the real parser's own output through the
    ingest and confirms a stock genotyped through CyO still predicts Cy
    afterward.
    """
    html = """
    <h2>Second Chromosome Balancers</h2>
    <table>
      <tr><th>Balancer</th><th>Chromosome</th><th>Genotype</th>
          <th>Dominant Markers</th><th>Comments</th></tr>
      <tr><td>CyO</td><td>2</td><td>Cy dp[lvI] pr cn[2]</td><td>Cy</td>
          <td>Standard second-chromosome balancer.</td></tr>
    </table>
    """
    parsed = parse_bdsc_balancer_definitions_html(
        html, source_url="https://bdsc.example/balancers")
    balancer = next(b for b in parsed["balancers"] if b["symbol"] == "CyO")
    # Confirm the fixture below is exercising what the real parser actually
    # emits, not a stale guess at its shape.
    assert balancer["marker_tokens"] == ["Cy"]
    assert isinstance(balancer["notes"], str)
    assert "default_markers" not in balancer
    assert "aliases" not in balancer
    assert "family" not in balancer

    genotype = "w[1118]; CyO/Sp; +; +"
    before = predict_stock_phenotype(genotype)["best_guess_summary"]
    assert "Cy" in before, "sanity check: CyO must predict Cy before any ingest"

    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, {"definitions": parsed})
    marker_catalog.refresh_catalog(db, force=True)

    after = predict_stock_phenotype(genotype)["best_guess_summary"]
    assert "Cy" in after, "re-ingesting CyO must not strip its markers"
