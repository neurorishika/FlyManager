# Cron Job Locking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three APScheduler cron jobs in `flymanager/app/__init__.py` safe under multi-worker execution, per `RESILIENCE_STANDARD_GUIDELINE.md` section 4, using the existing Mongo-backed `hold_operation_lock` primitive.

**Architecture:** A single wrapper function, `run_locked_scheduled_job`, is added to `flymanager/app/__init__.py`. Each of the three `scheduler.add_job(...)` registrations wraps its target function with this helper (via `functools.partial`) and adds `max_instances=1, coalesce=True`. The pre-existing `fcntl`-based file lock in `schedule_daily_flip_reminders` is removed since the new DB lock supersedes it.

**Tech Stack:** Python, Flask, Flask-APScheduler, pymongo (via existing `flymanager.utils.mongo.operation_locks`), pytest.

## Global Constraints

- Do not modify the business logic inside `schedule_daily_flip_reminders`, `update_flybase_reference_data`, or `update_bloomington_stock_data` beyond removing the `fcntl` lock in the first one.
- Lock TTLs: 1800s for the daily job, 3600s for each monthly job (see spec `docs/superpowers/specs/2026-07-09-cron-job-locking-design.md`).
- A lock conflict must be logged and swallowed (not raised) so APScheduler never sees it as a job failure.
- No new dependencies.

---

## Task 1: Add `run_locked_scheduled_job` wrapper and wire it into the three scheduler registrations

**Files:**
- Modify: `flymanager/app/__init__.py` (imports at top, and the `scheduler.add_job` block currently at lines 274-308)
- Test: `tests/test_scheduled_job_locking.py` (new)

**Interfaces:**
- Produces: `run_locked_scheduled_job(app, *, key, label, ttl_seconds, func)` — a module-level function in `flymanager/app/__init__.py`. Calling it acquires `hold_operation_lock(db, key=f"cron:{key}", ...)`, calls `func(app)` if the lock is acquired, and on `OperationLockConflict` logs via `app.logger.info(...)` and returns `None` without raising.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduled_job_locking.py`:

```python
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
```

Note: `db` here is a plain `dict`. `hold_operation_lock`/`_get_operation_lock_collection` fall back to `_InMemoryOperationLockCollection` via `db.setdefault(...)` when `db["operation_locks"]` raises `KeyError` (see `flymanager/utils/mongo/operation_locks.py:99-105`), so a plain dict works as the fake `db` in tests exactly as it does in `tests/test_background_jobs.py`.

`monkeypatch.setattr("flymanager.app.db", db)` replaces the module-level `db` that `run_locked_scheduled_job` reads at call time — `flymanager/app/__init__.py` already defines a module-level `db` variable (assigned by `create_app()`), so this patches it for the duration of each test without requiring a real Mongo connection or a full `create_app()` call.

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/test_scheduled_job_locking.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_locked_scheduled_job' from 'flymanager.app'`

- [ ] **Step 3: Add the wrapper function and update imports in `flymanager/app/__init__.py`**

Modify the import block at lines 25-29 to add `OperationLockConflict` and `hold_operation_lock`:

```python
from flymanager.utils.mongo import (OperationLockConflict, create_mongo_client,
                                    ensure_mongo_indexes, get_all_users,
                                    get_database, get_flip_schedule,
                                    get_settings, get_user_email,
                                    hold_operation_lock, ping_database,
                                    preload_metadata_cache)
```

Add `import functools` to the top-level imports (after `import os`):

```python
import functools
import os
import re
import secrets
from datetime import timedelta
```

Add the wrapper function immediately above `def create_app():` at `flymanager/app/__init__.py:97`:

```python
def run_locked_scheduled_job(app, *, key, label, ttl_seconds, func):
    """Run a scheduled job under a Mongo-backed distributed lock.

    Guards APScheduler cron jobs against double-firing if the app is ever
    scaled to more than one replica. A lock conflict is a routine, expected
    skip (another replica already grabbed this tick), logged and swallowed
    rather than raised, so APScheduler never records it as a job failure.
    """
    with app.app_context():
        try:
            with hold_operation_lock(
                db,
                key=f"cron:{key}",
                actor="scheduler",
                label=label,
                ttl_seconds=ttl_seconds,
                conflict_message=f"{label} is already running on another instance; skipping.",
            ):
                func(app)
        except OperationLockConflict as exc:
            app.logger.info("Skipped scheduled job %s: %s", key, exc)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/test_scheduled_job_locking.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Wire the wrapper into the three `scheduler.add_job` calls**

Replace the scheduler block (originally lines 274-308) with:

```python
        # --- Initialize Scheduler ---
        if env_flag("ENABLE_SCHEDULER", True) and not scheduler.running:
            # Add scheduled job using the function from services
            scheduler.add_job(
                id="daily_flip_reminder_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="daily_flip_reminder",
                    label="Daily flip reminder",
                    ttl_seconds=1800,
                    func=scheduler_service.schedule_daily_flip_reminders,
                ),
                trigger="cron",
                hour=8,
                minute=0,
                args=[app],  # Pass the app instance to the scheduled function
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            # Add monthly FlyBase reference refresh job (1st day of each month at 1:30 AM)
            scheduler.add_job(
                id="monthly_flybase_reference_refresh_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="monthly_flybase_reference_refresh",
                    label="Monthly FlyBase reference refresh",
                    ttl_seconds=3600,
                    func=flybase_service.update_flybase_reference_data,
                ),
                trigger="cron",
                day=1,
                hour=1,
                minute=30,
                args=[app],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            # Add monthly legacy Bloomington compatibility refresh job (1st day of each month at 2 AM)
            scheduler.add_job(
                id="monthly_bloomington_update_job",
                func=functools.partial(
                    run_locked_scheduled_job,
                    key="monthly_bloomington_update",
                    label="Monthly Bloomington compatibility refresh",
                    ttl_seconds=3600,
                    func=bloomington_service.update_bloomington_stock_data,
                ),
                trigger="cron",
                day=1,
                hour=2,
                minute=0,
                args=[app],
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            app.logger.info("Scheduler started.")
```

- [ ] **Step 6: Run the full test suite to verify nothing else broke**

Run: `poetry run pytest tests/test_scheduled_job_locking.py tests/test_background_jobs.py -v`
Expected: PASS (all tests in both files)

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/__init__.py tests/test_scheduled_job_locking.py
git commit -m "$(cat <<'EOF'
Guard APScheduler cron jobs with distributed Mongo lock

Wraps the three scheduled jobs (daily flip reminder, monthly FlyBase
refresh, monthly Bloomington refresh) with the existing
hold_operation_lock primitive and sets max_instances=1/coalesce=true,
so they stay safe if the app is ever scaled to more than one replica.
EOF
)"
```

---

## Task 2: Remove the redundant `fcntl` file lock from `schedule_daily_flip_reminders`

**Files:**
- Modify: `flymanager/app/services/scheduler.py`
- Test: `tests/test_scheduler.py` (existing manual script — update to match new signature/behavior if needed; no new automated test required since Task 1's tests already cover the locking behavior at the call-site level)

**Interfaces:**
- Consumes: nothing new.
- Produces: `schedule_daily_flip_reminders(app)` with the same external signature and behavior (sends flip reminder emails to all users), minus the internal `/tmp` file lock.

- [ ] **Step 1: Rewrite `flymanager/app/services/scheduler.py`**

```python
import time

from flymanager.app import db, get_all_users
from flymanager.app.services.email import send_flip_reminder_email


def schedule_daily_flip_reminders(app):
    """
    Scheduled task function to send flip reminders to all users.
    Needs to run within an app context. Concurrency safety is handled by
    the distributed lock in flymanager.app.run_locked_scheduled_job, which
    wraps every call site of this function.
    """
    with app.app_context():
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Running daily flip reminder task...")

        try:
            all_users_dict = get_all_users(db)
            if not all_users_dict:
                print(f"[{timestamp}] No users found in the database.")
                return

            all_usernames = list(all_users_dict.keys())
            print(f"[{timestamp}] Found users: {all_usernames}")

            for username in all_usernames:
                print(f"[{timestamp}] Sending reminder for user: {username}")
                send_flip_reminder_email(username)
            print(f"[{timestamp}] Finished sending daily flip reminders.")

        except Exception as e:
            print(f"[{timestamp}] Error during scheduled flip reminder task: {e}")


# The job addition and starting is handled in app/__init__.py
```

This removes the `import os`, `import fcntl`, and the file-lock try/except/finally wrapper. The inner `try`/`except`/`with app.app_context()` body is otherwise byte-for-byte the same logic.

- [ ] **Step 2: Run the existing scheduler test script to confirm the function still runs cleanly**

Run: `poetry run python tests/test_scheduler.py`
Expected: Output ends with `✅ Function executed successfully!` (requires a reachable Mongo instance per that script's existing behavior — this is a pre-existing manual script, not being changed in this task).

- [ ] **Step 3: Run the full Phase 1 test file again to make sure the app still imports cleanly**

Run: `poetry run pytest tests/test_scheduled_job_locking.py -v`
Expected: PASS (all 5 tests) — confirms `flymanager/app/__init__.py` still imports `scheduler_service.schedule_daily_flip_reminders` without error after the rewrite.

- [ ] **Step 4: Commit**

```bash
git add flymanager/app/services/scheduler.py
git commit -m "$(cat <<'EOF'
Remove redundant /tmp fcntl lock from flip reminder job

The distributed Mongo lock added around every scheduled-job call site
(flymanager.app.run_locked_scheduled_job) now supersedes this
single-container-only file lock.
EOF
)"
```

---

## Task 3: Manual verification against the running app

**Files:** none (verification only, no code changes)

- [ ] **Step 1: Start the app locally with the scheduler enabled**

Run: `poetry run python -m flask --app flymanager.app run` (or whatever the project's existing local-run command is — check `README.md`/`Makefile` if this differs) with `ENABLE_SCHEDULER=1` in the environment.

- [ ] **Step 2: Confirm scheduler startup log line appears**

Expected in stdout/logs: `Scheduler started.`

- [ ] **Step 3: Manually invoke the wrapper twice in a Python shell to confirm the skip path logs correctly**

Run: `poetry run python`

```python
from flymanager.app import create_app, run_locked_scheduled_job, db
from flymanager.utils.mongo import hold_operation_lock

app = create_app()
with app.app_context():
    with hold_operation_lock(db, key="cron:daily_flip_reminder", actor="scheduler", label="Daily flip reminder"):
        run_locked_scheduled_job(app, key="daily_flip_reminder", label="Daily flip reminder", ttl_seconds=1800, func=lambda a: print("SHOULD NOT RUN"))
```

Expected: no `SHOULD NOT RUN` printed; an INFO log line like `Skipped scheduled job daily_flip_reminder: ...` appears instead.

- [ ] **Step 4: Report result to the user**

No commit for this task — it's a verification checkpoint. Summarize the manual test result before moving to Phase 2 planning.
