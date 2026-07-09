"""Tests for the distributed-lock wrapper around APScheduler cron jobs.

Loaded via the real package import (unlike test_background_jobs.py) because
run_locked_scheduled_job lives in flymanager.app.__init__ and is exercised
with a fake app + in-memory lock collection, not a live Mongo connection.
"""
import logging

import pytest

from flymanager.app import run_locked_scheduled_job
from flymanager.utils.mongo.operation_locks import (
    OperationLockConflict, hold_operation_lock)


class _FakeAppContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeApp:
    def __init__(self):
        self.logger = logging.getLogger("fake-app-test")

    def app_context(self):
        return _FakeAppContext()


@pytest.fixture
def db():
    return {}


@pytest.fixture
def app():
    return _FakeApp()


def test_run_locked_scheduled_job_executes_func_when_lock_is_free(app, db, monkeypatch):
    monkeypatch.setattr("flymanager.app.db", db)
    calls = []

    run_locked_scheduled_job(
        app, key="test_job", label="Test Job", ttl_seconds=60,
        func=lambda a: calls.append(a),
    )

    assert calls == [app]


def test_run_locked_scheduled_job_skips_when_already_locked(app, db, monkeypatch, caplog):
    monkeypatch.setattr("flymanager.app.db", db)
    calls = []

    with hold_operation_lock(db, key="cron:test_job", actor="scheduler", label="Test Job"):
        with caplog.at_level(logging.INFO):
            run_locked_scheduled_job(
                app, key="test_job", label="Test Job", ttl_seconds=60,
                func=lambda a: calls.append(a),
            )

    assert calls == []
    assert any("test_job" in message for message in caplog.messages)


def test_run_locked_scheduled_job_allows_rerun_after_lock_released(app, db, monkeypatch):
    monkeypatch.setattr("flymanager.app.db", db)
    calls = []

    run_locked_scheduled_job(
        app, key="test_job", label="Test Job", ttl_seconds=60,
        func=lambda a: calls.append("first"),
    )
    run_locked_scheduled_job(
        app, key="test_job", label="Test Job", ttl_seconds=60,
        func=lambda a: calls.append("second"),
    )

    assert calls == ["first", "second"]


def test_run_locked_scheduled_job_different_keys_do_not_block_each_other(app, db, monkeypatch):
    monkeypatch.setattr("flymanager.app.db", db)
    calls = []

    with hold_operation_lock(db, key="cron:job_a", actor="scheduler", label="Job A"):
        run_locked_scheduled_job(
            app, key="job_b", label="Job B", ttl_seconds=60,
            func=lambda a: calls.append("job_b"),
        )

    assert calls == ["job_b"]


def test_run_locked_scheduled_job_never_raises_operation_lock_conflict(app, db, monkeypatch):
    monkeypatch.setattr("flymanager.app.db", db)

    with hold_operation_lock(db, key="cron:test_job", actor="scheduler", label="Test Job"):
        try:
            run_locked_scheduled_job(
                app, key="test_job", label="Test Job", ttl_seconds=60,
                func=lambda a: None,
            )
        except OperationLockConflict:
            pytest.fail("run_locked_scheduled_job must swallow OperationLockConflict")
