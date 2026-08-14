from unittest.mock import ANY, patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.app.jobs.tasks import _force_recompute_cache, task_force_recompute_cache


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


def test_task_force_recompute_cache_calls_dispatcher_then_records_then_logs_activity_in_order():
    """task_force_recompute_cache builds a work(app, db) closure and hands it to
    _run's mark-running/succeeded/failed envelope. We don't have a real worker
    app/Mongo replica set available for _run itself here, so we patch _run to
    execute the closure immediately (as it would in production) and assert on
    the call order of the three side effects the closure performs."""
    db = FakeDatabase({"stocks": [], "crosses": []})
    call_order = []

    def fake_run(key, work):
        work(None, db)

    def fake_force_recompute_cache(cache_key, db):
        call_order.append("_force_recompute_cache")
        return {"stocks": {"updated": 1}}

    def fake_record_force_refresh(cache_key, username, db):
        call_order.append("record_force_refresh")

    def fake_write_activity(username, message, db):
        call_order.append("write_activity")

    with patch("flymanager.app.jobs.tasks._run", side_effect=fake_run) as run_mock, patch(
        "flymanager.app.jobs.tasks._force_recompute_cache",
        side_effect=fake_force_recompute_cache,
    ) as force_recompute_mock, patch(
        "flymanager.utils.mongo.cache_force_refresh.record_force_refresh",
        side_effect=fake_record_force_refresh,
    ) as record_mock, patch(
        "flymanager.app.jobs.tasks.write_activity", side_effect=fake_write_activity
    ) as write_activity_mock:
        task_force_recompute_cache("job-key", "admin", "phenotype")

    run_mock.assert_called_once()
    force_recompute_mock.assert_called_once_with("phenotype", db)
    record_mock.assert_called_once_with("phenotype", "admin", db)
    write_activity_mock.assert_called_once()
    assert call_order == ["_force_recompute_cache", "record_force_refresh", "write_activity"]


def test_task_force_recompute_cache_skips_record_and_activity_when_dispatcher_raises():
    db = FakeDatabase({"stocks": [], "crosses": []})
    call_order = []

    def fake_run(key, work):
        work(None, db)

    def fake_force_recompute_cache(cache_key, db):
        call_order.append("_force_recompute_cache")
        raise RuntimeError("boom")

    def fake_record_force_refresh(cache_key, username, db):
        call_order.append("record_force_refresh")

    def fake_write_activity(username, message, db):
        call_order.append("write_activity")

    with patch("flymanager.app.jobs.tasks._run", side_effect=fake_run), patch(
        "flymanager.app.jobs.tasks._force_recompute_cache",
        side_effect=fake_force_recompute_cache,
    ), patch(
        "flymanager.utils.mongo.cache_force_refresh.record_force_refresh",
        side_effect=fake_record_force_refresh,
    ), patch(
        "flymanager.app.jobs.tasks.write_activity", side_effect=fake_write_activity
    ):
        try:
            task_force_recompute_cache("job-key", "admin", "phenotype")
            assert False, "expected RuntimeError to propagate out of the work closure"
        except RuntimeError:
            pass

    assert call_order == ["_force_recompute_cache"]
