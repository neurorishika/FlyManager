"""Tests for flymanager.app.jobs (Redis/RQ enqueue wiring).

Loaded in isolation with stubbed flymanager.utils.mongo.* modules - importing
flymanager.app.jobs normally pulls in the flymanager.app package (which opens
a real MongoDB connection at import time), which we don't need here: this
module's own logic only depends on the (already independently-tested)
operation_locks status-tracking functions plus Redis/RQ.
"""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import fakeredis
import pytest
from rq import Queue

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_operation_locks_module():
    path = REPO_ROOT / "flymanager" / "utils" / "mongo" / "operation_locks.py"
    spec = importlib.util.spec_from_file_location("test_jobs_operation_locks", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_STUBBED_MODULE_NAMES = (
    "flymanager",
    "flymanager.utils",
    "flymanager.utils.mongo",
    "flymanager.utils.mongo.operation_locks",
)


def _load_jobs_module(operation_locks_module):
    """Load flymanager/app/jobs/__init__.py with its flymanager.utils.mongo.*
    imports satisfied by stubs, without permanently clobbering sys.modules
    for any other test in the same process that imports the real packages.
    """
    flymanager_module = ModuleType("flymanager")
    flymanager_module.__path__ = []
    utils_module = ModuleType("flymanager.utils")
    utils_module.__path__ = []
    mongo_module = ModuleType("flymanager.utils.mongo")
    mongo_module.__path__ = []
    mongo_module.operation_locks = operation_locks_module

    saved_modules = {name: sys.modules.get(name) for name in _STUBBED_MODULE_NAMES}
    sys.modules["flymanager"] = flymanager_module
    sys.modules["flymanager.utils"] = utils_module
    sys.modules["flymanager.utils.mongo"] = mongo_module
    sys.modules["flymanager.utils.mongo.operation_locks"] = operation_locks_module

    try:
        path = REPO_ROOT / "flymanager" / "app" / "jobs" / "__init__.py"
        spec = importlib.util.spec_from_file_location("test_jobs_module", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in saved_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


@pytest.fixture
def ol():
    return _load_operation_locks_module()


@pytest.fixture
def jobs(ol, monkeypatch):
    module = _load_jobs_module(ol)
    fake_connection = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(module, "get_redis_connection", lambda: fake_connection)
    monkeypatch.setattr(module, "_queue", None)
    return module


def _noop_task(key, **kwargs):
    return None


def test_enqueue_job_creates_status_record_and_queues_it(jobs, ol):
    db = {}
    key = jobs.enqueue_job(
        db,
        key="job:export",
        actor="alice",
        label="Excel export",
        func=_noop_task,
        kwargs={"key": "job:export"},
    )
    assert key == "job:export"
    status = ol.get_job_status(db, key)
    assert status["status"] == ol.JOB_STATUS_QUEUED

    queue = Queue(jobs.DEFAULT_QUEUE_NAME, connection=jobs.get_redis_connection())
    assert queue.count == 1


def test_enqueue_job_raises_conflict_when_already_active(jobs, ol):
    db = {}
    jobs.enqueue_job(
        db, key="job:export", actor="alice", label="Excel export",
        func=_noop_task, kwargs={"key": "job:export"},
    )

    with pytest.raises(ol.OperationLockConflict):
        jobs.enqueue_job(
            db, key="job:export", actor="alice", label="Excel export",
            func=_noop_task, kwargs={"key": "job:export"},
            conflict_message="Already running.",
        )

    # Only the first enqueue should have actually reached the queue.
    queue = Queue(jobs.DEFAULT_QUEUE_NAME, connection=jobs.get_redis_connection())
    assert queue.count == 1
