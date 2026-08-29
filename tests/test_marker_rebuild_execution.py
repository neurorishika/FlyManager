import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.marker_rebuild import \
    rebuild_after_marker_change
from flymanager.utils.phenotypes.predictor import build_stock_phenotype_cache
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _stock(unique_id, genotype, *, cache=True, stale_genotype=False):
    record = {"_id": unique_id, "UniqueID": unique_id, "User": "admin",
              "AssignedTo": "", "Genotype": genotype}
    if cache:
        cached_genotype = "something else" if stale_genotype else genotype
        record["PhenotypeCache"] = build_stock_phenotype_cache(cached_genotype)
    return record


def _mutate_catalog_and_bump(db):
    """Simulate the write path: edit a marker, bump the revision, refresh."""
    db["marker_definitions"].insert_one({
        "Key": "Cy",
        "kind": "gene_marker",
        "match": {"symbol": "Cy"},
        "payload": {"body_part": "wing", "effect": "edited curly wings",
                    "dominance": "dominant", "display_label": "Cy",
                    "phenotype_key": "Cy", "chromosome": 2,
                    "scoring_confidence": 0.95},
        "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
        "provenance": {"source": "user"}, "origin": "user",
    })
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 1}})
    marker_catalog.refresh_catalog(db, force=True)


def _db(stocks, crosses=()):
    return FakeDatabase({
        "stocks": list(stocks),
        "crosses": list(crosses),
        "marker_definitions": [],
        "settings": [{"markerCatalogRevision": 0}],
    })


def test_only_records_containing_an_affected_token_are_recomputed():
    db = _db([_stock("A", "w[1118]; CyO/Sp; +; +"), _stock("B", "w[1118]; +; +; +")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["Cy"])

    assert result["scope"] == "targeted"
    signature = marker_catalog.get_catalog()["signature"]
    records = {r["UniqueID"]: r for r in db["stocks"].find({})}
    assert records["A"]["PhenotypeCache"]["markerCatalogSignature"] == signature
    assert "edited curly wings" in str(records["A"]["PhenotypeCache"]["prediction"])
    assert records["B"]["PhenotypeCache"]["markerCatalogSignature"] == signature
    assert "edited curly wings" not in str(records["B"]["PhenotypeCache"]["prediction"])


def test_a_balancer_referencing_the_edited_marker_is_swept():
    db = _db([_stock("A", "w[1118]; SM6a/Sp; +; +")])
    _mutate_catalog_and_bump(db)

    rebuild_after_marker_change(db, ["Cy"])

    cache = db["stocks"].find_one({"UniqueID": "A"})["PhenotypeCache"]
    assert "edited curly wings" in str(cache["prediction"])


def test_records_stale_for_other_reasons_are_not_stamped():
    db = _db([_stock("C", "w[1118]; +; +; +", stale_genotype=True)])
    _mutate_catalog_and_bump(db)

    rebuild_after_marker_change(db, ["Cy"])

    cache = db["stocks"].find_one({"UniqueID": "C"})["PhenotypeCache"]
    assert cache["markerCatalogSignature"] != marker_catalog.get_catalog()["signature"]


def test_records_with_no_cache_are_left_alone_by_stamping():
    db = _db([_stock("D", "w[1118]; +; +; +", cache=False)])
    _mutate_catalog_and_bump(db)

    rebuild_after_marker_change(db, ["Cy"])

    assert "PhenotypeCache" not in db["stocks"].find_one({"UniqueID": "D"})


def test_a_construct_marker_edit_rebuilds_everything():
    db = _db([_stock("A", "w[1118]; CyO/Sp; +; +"), _stock("B", "w[1118]; +; +; +")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["construct:w+"])

    assert result["scope"] == "full"
    assert result["stocks"]["rebuilt"] == 2
    # Nothing left to stamp: the rebuild already wrote the new signature onto
    # every record, and stamping skips caches that already carry it.
    assert result["stocks"]["stamped"] == 0


def test_a_removed_balancer_alias_is_swept_when_the_prior_document_is_given():
    db = _db([_stock("A", "w[1118]; Binsn/Y; +; +")])
    _mutate_catalog_and_bump(db)
    previous = {"Key": "Binsc", "kind": "balancer",
                "match": {"symbol": "Binsc", "aliases": ["Binsn"]}}

    result = rebuild_after_marker_change(db, ["Binsc"], previous_documents=[previous])

    assert "Binsn" in result["tokens"]
    assert result["stocks"]["rebuilt"] == 1


def test_deleting_a_shipped_override_rebuilds_everything():
    db = _db([_stock("A", "w[1118]; CyO/Sp; +; +")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["Cy"], deleted_override_keys=["Cy"])

    assert result["scope"] == "full"


def test_crosses_are_swept_on_both_genotype_fields():
    db = _db([], crosses=[{
        "_id": "X", "UniqueID": "X", "User": "admin", "AssignedTo": "",
        "MaleGenotype": "w[1118]; +; +; +", "FemaleGenotype": "w[1118]; CyO/Sp; +; +",
    }])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["Cy"])

    assert result["crosses"]["rebuilt"] == 1
    cache = db["crosses"].find_one({"UniqueID": "X"})["PhenotypeCache"]
    assert cache["markerCatalogSignature"] == marker_catalog.get_catalog()["signature"]


def test_an_empty_scope_stamps_without_rebuilding_anything():
    db = _db([_stock("A", "w[1118]; CyO/Sp; +; +")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, [])

    assert result["scope"] == "targeted"
    assert result["stocks"]["rebuilt"] == 0
    assert result["stocks"]["stamped"] == 1
