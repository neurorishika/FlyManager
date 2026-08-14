from unittest.mock import ANY, patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.app.jobs.tasks import _force_recompute_cache


def test_force_recompute_phenotype_calls_stock_and_cross_backfill_with_force_true():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.utils.phenotypes.backfill.backfill_stock_phenotype_cache",
        return_value={"scanned": 2, "updated": 2, "skipped_valid": 0},
    ) as stock_backfill, patch(
        "flymanager.utils.phenotypes.backfill.backfill_cross_phenotype_cache",
        return_value={"scanned": 1, "updated": 1, "skipped_valid": 0},
    ) as cross_backfill:
        summary = _force_recompute_cache("phenotype", db)

    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    cross_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    assert summary == {
        "stocks": {"scanned": 2, "updated": 2, "skipped_valid": 0},
        "crosses": {"scanned": 1, "updated": 1, "skipped_valid": 0},
    }


def test_force_recompute_standardization_calls_stock_and_cross_backfill_with_force_true():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.app.services.standardization_backfill.backfill_stock_standardization_cache",
        return_value={"scanned": 3, "updated": 1, "skipped_valid": 2},
    ) as stock_backfill, patch(
        "flymanager.app.services.standardization_backfill.backfill_cross_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_backfill:
        summary = _force_recompute_cache("standardization", db)

    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    cross_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    assert summary["stocks"]["updated"] == 1


def test_force_recompute_provider_match_calls_stock_backfill_only():
    db = FakeDatabase({"stocks": []})

    with patch(
        "flymanager.app.routes.stock.backfill_stock_provider_match_cache",
        return_value={"scanned": 4, "updated": 4, "skipped_valid": 0, "errors": 0},
    ) as stock_backfill:
        summary = _force_recompute_cache("provider_match", db)

    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    assert summary == {"stocks": {"scanned": 4, "updated": 4, "skipped_valid": 0, "errors": 0}}


def test_force_recompute_unknown_cache_key_raises_value_error():
    db = FakeDatabase()
    try:
        _force_recompute_cache("nonexistent", db)
        assert False, "expected ValueError"
    except ValueError:
        pass
