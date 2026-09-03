"""Regression coverage for asynchronous derived-cache materialization."""
import time
from unittest.mock import patch

import flymanager.app  # noqa: F401

from flymanager.utils import cache_generation
from flymanager.utils.mongo.stocks import add_to_stock, edit_stock
from tests.mongo_fakes import FakeDatabase


class OperationLockConflict(Exception):
    pass


def _properties():
    return {
        "SourceID": "source-1", "StockSource": "OTHER",
        "Species": "D. melanogaster", "SeriesID": "series-1",
        "ReplicateID": "1", "Type": "WT", "Status": "Healthy",
        "FoodType": "Molasses", "VialLifetime": "14", "FlipFrequency": "7",
        "DevelopmentalTime": "10", "Provenance": "test", "Genotype": "w[*]",
        "Name": "cold-cache regression stock",
    }


def test_stock_create_persists_promptly_without_calling_cache_builders():
    db = FakeDatabase()
    with patch("flymanager.utils.mongo.stocks.schedule_record_cache_generation") as schedule:
        started_at = time.perf_counter()
        success, uid = add_to_stock("alice", _properties(), db, submission_key="form-1")
        elapsed_ms = (time.perf_counter() - started_at) * 1000

    assert success is True
    record = db["stocks"].find_one({"UniqueID": uid})
    assert record["PhenotypeCache"] is None
    assert record["StandardizationCache"] is None
    assert record["CacheGeneration"]["state"] == "pending"
    assert record["CacheGeneration"]["jobKey"].startswith("cache-generation:stock:")
    schedule.assert_called_once()
    # The regression is architectural: no FlyBase builder is reachable from
    # this cold create path, so a simple document insert stays well below a
    # user-facing latency budget even when worker cache construction is slow.
    assert elapsed_ms < 250


def test_stock_retry_with_same_submission_key_returns_existing_record():
    db = FakeDatabase()
    with patch("flymanager.utils.mongo.stocks.schedule_record_cache_generation") as schedule:
        first_success, first_uid = add_to_stock("alice", _properties(), db, submission_key="form-1")
        second_success, second_uid = add_to_stock("alice", _properties(), db, submission_key="form-1")

    assert first_success and second_success
    assert first_uid == second_uid
    assert db["stocks"].count_documents({}) == 1
    assert schedule.call_count == 2  # retried dispatch, never retried insert


def test_genotype_edit_marks_cache_pending_and_schedules_worker_work():
    db = FakeDatabase({"stocks": [{
        "UniqueID": "SID1", "User": "alice", "Genotype": "w[*]",
        "PhenotypeCache": {"old": True}, "StandardizationCache": {"old": True},
    }]})
    with patch("flymanager.utils.mongo.stocks.schedule_record_cache_generation") as schedule, patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
        return_value={"crosses_updated": 0, "errors": 0},
    ):
        assert edit_stock("alice", "SID1", db, {"Genotype": "w[*]; CyO/+"}, refresh_vials=False)

    record = db["stocks"].find_one({"UniqueID": "SID1"})
    assert record["PhenotypeCache"] is None
    assert record["StandardizationCache"] is None
    assert record["CacheGeneration"]["state"] == "pending"
    schedule.assert_called_once()


def test_background_materialization_publishes_both_caches_only_for_current_input():
    signature = cache_generation.stock_cache_signature("w[*]")
    db = FakeDatabase({"stocks": [{
        "UniqueID": "SID1", "User": "alice", "Genotype": "w[*]",
        "CacheGeneration": cache_generation.pending_cache_generation("stock", "SID1", signature),
    }]})
    with patch(
        "flymanager.utils.phenotypes.predictor.build_stock_phenotype_cache",
        return_value={"phenotype": "ready"},
    ), patch(
        "flymanager.app.services.stock_standardization.build_stock_standardization_cache",
        return_value={"standardization": "ready"},
    ):
        result = cache_generation.materialize_record_caches(
            db, record_type="stock", unique_id="SID1", actor="alice", input_signature=signature,
        )

    assert result == {"status": "ready"}
    record = db["stocks"].find_one({"UniqueID": "SID1"})
    assert record["PhenotypeCache"] == {"phenotype": "ready"}
    assert record["StandardizationCache"] == {"standardization": "ready"}
    assert record["CacheGeneration"]["state"] == "ready"


def test_stale_background_job_cannot_overwrite_a_newer_genotype():
    signature = cache_generation.stock_cache_signature("w[*]")
    newer_signature = cache_generation.stock_cache_signature("w[*]; CyO/+")
    db = FakeDatabase({"stocks": [{
        "UniqueID": "SID1", "User": "alice", "Genotype": "w[*]; CyO/+",
        "CacheGeneration": cache_generation.pending_cache_generation("stock", "SID1", newer_signature),
    }]})

    result = cache_generation.materialize_record_caches(
        db, record_type="stock", unique_id="SID1", actor="alice", input_signature=signature,
    )

    assert result == {"status": "superseded"}
    assert db["stocks"].find_one({"UniqueID": "SID1"}).get("PhenotypeCache") is None


def test_failed_worker_job_is_visible_on_the_record():
    signature = cache_generation.stock_cache_signature("w[*]")
    db = FakeDatabase({"stocks": [{
        "UniqueID": "SID1", "User": "alice", "Genotype": "w[*]",
        "CacheGeneration": cache_generation.pending_cache_generation("stock", "SID1", signature),
    }]})

    cache_generation.fail_record_cache_generation(
        db, record_type="stock", unique_id="SID1", actor="alice",
        input_signature=signature, error=RuntimeError("FlyBase unavailable"),
    )

    state = db["stocks"].find_one({"UniqueID": "SID1"})["CacheGeneration"]
    assert state["state"] == "failed"
    assert state["phenotype"]["state"] == "failed"
    assert "FlyBase unavailable" in state["error"]


def test_enqueue_failure_is_visible_without_rolling_back_the_record():
    signature = cache_generation.stock_cache_signature("w[*]")
    db = FakeDatabase({"stocks": [{
        "UniqueID": "SID1", "User": "alice", "Genotype": "w[*]",
        "CacheGeneration": cache_generation.pending_cache_generation("stock", "SID1", signature),
    }]})
    with patch(
        "flymanager.utils.cache_generation.enqueue_record_cache_generation",
        side_effect=RuntimeError("Redis unavailable"),
    ):
        assert cache_generation.schedule_record_cache_generation(
            db, actor="alice", record_type="stock", unique_id="SID1", input_signature=signature,
        ) is None

    state = db["stocks"].find_one({"UniqueID": "SID1"})["CacheGeneration"]
    assert state["state"] == "failed"
    assert "Redis unavailable" in state["error"]


def test_active_job_conflict_is_an_idempotent_enqueue_result():
    db = FakeDatabase()
    with patch(
        "flymanager.app.jobs.enqueue_job",
        side_effect=OperationLockConflict("already queued"),
    ):
        key = cache_generation.enqueue_record_cache_generation(
            db, actor="alice", record_type="stock", unique_id="SID1", input_signature="abc",
        )
    assert key == cache_generation.cache_job_key("stock", "SID1", "abc")
