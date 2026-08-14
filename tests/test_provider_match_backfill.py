from unittest.mock import patch

import flymanager.app  # noqa: F401  (see tests/test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.app.routes.stock import (PROVIDER_MATCH_CACHE_FIELD,
                                         PROVIDER_MATCH_CACHE_VERSION,
                                         backfill_stock_provider_match_cache)

SOURCE_CONTEXT = {
    "sourceType": "BDSC", "sourceCollection": "Bloomington",
    "flyBaseStockID": "FBst0000017", "providerURL": "",
}


def _stock(uid, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "AssignedTo": "",
        "StockSource": "BDSC", "SourceID": "17", "SourceCollection": "Bloomington",
        "FlyBaseStockID": "FBst0000017", "Genotype": "w[*]",
        "ExternalRawGenotype": "", "AltReference": "", "Provenance": "Bloomington",
    }
    stock.update(overrides)
    return stock


def test_backfill_stock_provider_match_cache_builds_missing_entries(monkeypatch):
    db = FakeDatabase({"stocks": [_stock("s1")]})
    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig",
    )

    with patch(
        "flymanager.app.routes.stock.enrich_stock_source_context",
        return_value=SOURCE_CONTEXT,
    ), patch(
        "flymanager.app.routes.stock.find_external_stock_matches",
        return_value=[{"stockSource": "VIENNA", "sourceID": "4321"}],
    ):
        summary = backfill_stock_provider_match_cache(db["stocks"])

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
    stored = db["stocks"].find_one({"UniqueID": "s1"})[PROVIDER_MATCH_CACHE_FIELD]
    assert stored["version"] == PROVIDER_MATCH_CACHE_VERSION
    assert stored["pipelineSignature"] == "pipeline-sig"
    assert stored["candidates"] == [{"stockSource": "VIENNA", "sourceID": "4321"}]


def test_backfill_stock_provider_match_cache_skips_already_valid_entry(monkeypatch):
    from flymanager.app.routes.stock import _build_provider_match_cache_signature

    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig",
    )
    stock = _stock("s1")
    stock[PROVIDER_MATCH_CACHE_FIELD] = {
        "version": PROVIDER_MATCH_CACHE_VERSION,
        "pipelineSignature": "pipeline-sig",
        "signature": _build_provider_match_cache_signature(stock, SOURCE_CONTEXT),
        "candidates": [],
        "count": 0,
        "cachedAt": "2026-04-17 10:00",
    }
    db = FakeDatabase({"stocks": [stock]})

    with patch(
        "flymanager.app.routes.stock.enrich_stock_source_context",
        return_value=SOURCE_CONTEXT,
    ), patch(
        "flymanager.app.routes.stock.find_external_stock_matches",
        side_effect=AssertionError("must not recompute an already-valid cache"),
    ):
        summary = backfill_stock_provider_match_cache(db["stocks"])

    assert summary == {"scanned": 1, "updated": 0, "skipped_valid": 1, "errors": 0}


def test_backfill_stock_provider_match_cache_counts_errors_without_aborting(monkeypatch):
    db = FakeDatabase({"stocks": [_stock("bad"), _stock("good")]})
    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig",
    )

    def fake_find_matches(record):
        if record["UniqueID"] == "bad":
            raise RuntimeError("reference data unavailable")
        return []

    with patch(
        "flymanager.app.routes.stock.enrich_stock_source_context",
        return_value=SOURCE_CONTEXT,
    ), patch(
        "flymanager.app.routes.stock.find_external_stock_matches",
        side_effect=fake_find_matches,
    ):
        summary = backfill_stock_provider_match_cache(db["stocks"])

    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 0, "errors": 1}
    assert db["stocks"].find_one({"UniqueID": "bad"}).get(PROVIDER_MATCH_CACHE_FIELD) is None
    assert db["stocks"].find_one({"UniqueID": "good"}).get(PROVIDER_MATCH_CACHE_FIELD) is not None
