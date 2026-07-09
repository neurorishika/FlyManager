# Cron Job Locking (Resilience Standard, Phase 1)

## Context

`RESILIENCE_STANDARD_GUIDELINE.md` requires every scheduled/background job to be
"safe under multi-worker execution," guarded by a database lock document with a
TTL, and registered with `max_instances=1`/`coalesce=true`.

A resilience audit of the current codebase found that the three APScheduler
cron jobs registered in `flymanager/app/__init__.py` do not meet this bar:

- `daily_flip_reminder_job` -> `scheduler_service.schedule_daily_flip_reminders`
  guards itself with a local `/tmp` `fcntl` file lock. This only prevents two
  *threads/processes on the same container* from double-firing; it does
  nothing if the app is ever scaled to more than one replica, since each
  container has its own `/tmp`.
- `monthly_flybase_reference_refresh_job` -> `flybase_service.update_flybase_reference_data`
  has no lock at all.
- `monthly_bloomington_update_job` -> `bloomington_service.update_bloomington_stock_data`
  has no lock at all.
- None of the three `scheduler.add_job(...)` calls set `max_instances` or
  `coalesce`, relying on APScheduler defaults.

This is the sharpest concrete gap found in the audit: the app already has a
working Mongo-backed distributed lock primitive (`hold_operation_lock` /
`OperationLockConflict` in `flymanager/utils/mongo/operation_locks.py`, with a
unique index + TTL on the lock collection), used for the new admin-triggered
background-job queue — but it is not applied to the pre-existing cron jobs
that are the actual multi-worker risk the standard is meant to close.

## Goal

Make all three scheduled jobs safe to run from more than one app replica,
using the lock primitive that already exists, without changing the business
logic inside `scheduler.py`, `flybase.py`, or `bloomington.py`.

## Design

### 1. Locked-run wrapper

Add a small helper, `run_locked_scheduled_job`, defined in
`flymanager/app/__init__.py` next to the existing scheduler setup:

```python
def run_locked_scheduled_job(app, *, key, label, ttl_seconds, func):
    with app.app_context():
        from flymanager.app import db
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

`func(app)` is the existing scheduled function unchanged (each of the three
already accepts `app` as its only argument). Wrapping happens at
registration time via `functools.partial`, so `scheduler.add_job(func=...)`
gets a zero-arg-besides-`app` callable it can invoke on the cron trigger
exactly as before.

A conflict is a routine, expected outcome (another replica already grabbed
the lock for this tick) — it is logged and swallowed, not raised, so
APScheduler doesn't log it as a job failure.

### 2. Registration changes

Each of the three `scheduler.add_job(...)` calls in
`flymanager/app/__init__.py`:

- wraps its `func` with `functools.partial(run_locked_scheduled_job, key=..., label=..., ttl_seconds=..., func=<original>)`
- adds `max_instances=1, coalesce=True`

TTLs (chosen to comfortably cover worst-case run time while staying well
inside the interval to the next scheduled tick, per the guideline's "TTL
slightly shorter than schedule interval"):

| Job | Interval | Lock TTL |
|---|---|---|
| `daily_flip_reminder_job` | daily | 1800s (30 min) |
| `monthly_flybase_reference_refresh_job` | monthly | 3600s (1 hour) |
| `monthly_bloomington_update_job` | monthly | 3600s (1 hour) |

### 3. Remove the redundant `fcntl` lock

`schedule_daily_flip_reminders` in `flymanager/app/services/scheduler.py`
drops its `/tmp/flip_reminder_lock` `fcntl` guard. The distributed DB lock at
the call site now supersedes it; keeping both would be confusing dead
weight and the file lock provides strictly less protection (single-container
only) than what replaces it. The rest of the function's behavior is
unchanged.

### 4. Tests

New `tests/test_scheduled_job_locking.py`, following the isolated-import
pattern already used by `tests/test_background_jobs.py` (importing
`operation_locks.py` directly to avoid pulling in the full Flask/Mongo app),
plus a lightweight fake `app` object exposing `app_context()` and `logger`.
Covers:

- Two concurrent calls to `run_locked_scheduled_job` with the same key: the
  second is skipped (logged), the wrapped `func` is only invoked once.
  jobs with different keys can run concurrently (no cross-job blocking).
- After the lock is released (context manager exit), a subsequent call with
  the same key runs normally.
- A conflict does not propagate as an exception out of
  `run_locked_scheduled_job` (so APScheduler never sees a job failure for a
  routine skip).

## Out of scope (deferred to later phases)

- Structured/queryable logging of every scheduled-job decision beyond the
  existing `app.logger` call (Phase 2/observability work).
- Applying `start_background_job`-style status history (queued/running/done)
  to these cron jobs — they're fast, in-process jobs; a simple mutex is
  sufficient and matches the guideline's minimum bar ("Guard each job with
  distributed locking").
- Any change to the RQ/Redis job queue, backup pipeline, replica set, or
  app-level idempotency tokens — those are Phases 2-4.
