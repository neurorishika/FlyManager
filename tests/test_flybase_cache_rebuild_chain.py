from unittest.mock import ANY, patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.app.jobs.tasks import _rebuild_caches_after_flybase_refresh


def test_rebuild_caches_after_flybase_refresh_calls_all_five_wrappers_with_force_false():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.utils.phenotypes.backfill.backfill_stock_phenotype_cache",
        return_value={"scanned": 1, "updated": 0, "skipped_valid": 1},
    ) as stock_phenotype, patch(
        "flymanager.utils.phenotypes.backfill.backfill_cross_phenotype_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_phenotype, patch(
        "flymanager.app.services.standardization_backfill.backfill_stock_standardization_cache",
        return_value={"scanned": 1, "updated": 0, "skipped_valid": 1},
    ) as stock_standardization, patch(
        "flymanager.app.services.standardization_backfill.backfill_cross_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_standardization, patch(
        "flymanager.app.routes.stock.backfill_stock_provider_match_cache",
        return_value={"scanned": 1, "updated": 0, "skipped_valid": 1, "errors": 0},
    ) as stock_provider_match:
        results = _rebuild_caches_after_flybase_refresh(db)

    stock_phenotype.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    cross_phenotype.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    stock_standardization.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    cross_standardization.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    stock_provider_match.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    assert results["stock_phenotype"]["skipped_valid"] == 1
    assert results["stock_provider_match"]["scanned"] == 1


def test_rebuild_caches_after_flybase_refresh_isolates_wrapper_failures():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.utils.phenotypes.backfill.backfill_stock_phenotype_cache",
        side_effect=RuntimeError("boom"),
    ), patch(
        "flymanager.utils.phenotypes.backfill.backfill_cross_phenotype_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_phenotype, patch(
        "flymanager.app.services.standardization_backfill.backfill_stock_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ), patch(
        "flymanager.app.services.standardization_backfill.backfill_cross_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ), patch(
        "flymanager.app.routes.stock.backfill_stock_provider_match_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0, "errors": 0},
    ):
        results = _rebuild_caches_after_flybase_refresh(db)

    assert results["stock_phenotype"] == {"error": True}
    cross_phenotype.assert_called_once()
    assert results["cross_phenotype"]["scanned"] == 0
