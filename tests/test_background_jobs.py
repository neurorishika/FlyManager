"""Tests for the background-job tracking layer (flymanager.utils.mongo.operation_locks).

Loaded in isolation (like test_tray_moves.py) because importing the module
through the normal `flymanager.utils.mongo` package pulls in flymanager.app,
which eagerly opens a real MongoDB connection at import time - unnecessary
for testing pure status-tracking logic that has zero flymanager imports of
its own.
"""
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "flymanager" / "utils" / "mongo" / "operation_locks.py"
MODULE_SPEC = importlib.util.spec_from_file_location("test_operation_locks_module", MODULE_PATH)
ol = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(ol)


@pytest.fixture
def db():
    return {}


def test_start_background_job_creates_queued_record(db):
    key = ol.start_background_job(db, key="job:1", actor="alice", label="Test job")
    status = ol.get_job_status(db, key)
    assert status["status"] == ol.JOB_STATUS_QUEUED
    assert status["actor"] == "alice"
    assert status["label"] == "Test job"
    assert status["result"] is None
    assert status["error"] is None


def test_start_background_job_conflicts_while_active(db):
    key = ol.start_background_job(db, key="job:1", actor="alice", label="Test job")
    with pytest.raises(ol.OperationLockConflict):
        ol.start_background_job(db, key=key, actor="bob", label="Test job again")


def test_start_background_job_conflicts_with_an_active_plain_mutex_lock(db):
    # A hold_operation_lock(s) mutex (e.g. a cron job's distributed lock,
    # used by flymanager.app.run_locked_scheduled_job) has no "status"
    # field - it must still block a start_background_job attempt under the
    # same key, the same as an active queued/running job would. Regression
    # test for a bug where such a lock was silently deleted and replaced.
    with ol.hold_operation_lock(db, key="maintenance:shared-key", actor="scheduler", label="Cron job"):
        with pytest.raises(ol.OperationLockConflict):
            ol.start_background_job(db, key="maintenance:shared-key", actor="alice", label="Manual trigger")


def test_start_background_job_allows_rerun_after_it_finished(db):
    key = ol.start_background_job(db, key="job:1", actor="alice", label="Test job")
    ol.mark_job_running(db, key)
    ol.mark_job_succeeded(db, key, result={"message": "done"})

    # A finished job doesn't block a fresh run under the same key.
    new_key = ol.start_background_job(db, key=key, actor="alice", label="Test job")
    assert new_key == key
    status = ol.get_job_status(db, key)
    assert status["status"] == ol.JOB_STATUS_QUEUED
    assert status["result"] is None


def test_job_status_transitions(db):
    key = ol.start_background_job(db, key="job:1", actor="alice", label="Test job")

    ol.mark_job_running(db, key)
    assert ol.get_job_status(db, key)["status"] == ol.JOB_STATUS_RUNNING
    assert ol.get_job_status(db, key)["started_at"] is not None

    ol.update_job_progress(db, key, current=3, total=10, message="Scanning...")
    progress = ol.get_job_status(db, key)["progress"]
    assert progress == {"current": 3, "total": 10, "message": "Scanning..."}

    ol.mark_job_succeeded(db, key, result={"message": "All done", "count": 10})
    status = ol.get_job_status(db, key)
    assert status["status"] == ol.JOB_STATUS_SUCCEEDED
    assert status["result"]["count"] == 10
    assert status["finished_at"] is not None


def test_job_status_failure(db):
    key = ol.start_background_job(db, key="job:1", actor="alice", label="Test job")
    ol.mark_job_running(db, key)
    ol.mark_job_failed(db, key, error=ValueError("boom"))

    status = ol.get_job_status(db, key)
    assert status["status"] == ol.JOB_STATUS_FAILED
    assert status["error"] == "boom"
    assert status["finished_at"] is not None


def test_get_job_status_ignores_plain_mutex_locks(db):
    """A plain hold_operation_lock mutex (no job status fields) isn't a job."""
    with ol.hold_operation_lock(db, key="record-mutation:STK-1", actor="alice", label="Editing"):
        assert ol.get_job_status(db, "record-mutation:STK-1") is None


def test_list_recent_jobs_filters_by_actor_and_sorts_newest_first(db):
    key_a = ol.start_background_job(db, key="job:a", actor="alice", label="Job A")
    ol.mark_job_running(db, key_a)
    ol.mark_job_succeeded(db, key_a, result={"message": "ok"})

    key_b = ol.start_background_job(db, key="job:b", actor="bob", label="Job B")

    alice_jobs = ol.list_recent_jobs(db, actor="alice")
    assert [job["key"] for job in alice_jobs] == ["job:a"]

    all_jobs = ol.list_recent_jobs(db)
    assert {job["key"] for job in all_jobs} == {"job:a", "job:b"}
