"""Background job infrastructure: Redis/RQ wiring on top of the Mongo-backed
job-status tracking in flymanager.utils.mongo.operation_locks.

Routes call enqueue_job(...) to hand heavy work off to the worker process
instead of running it inline. The worker process (flymanager.app.worker)
consumes the queue and calls the task functions in flymanager.app.jobs.tasks.
"""
import os

import redis
from rq import Queue

from flymanager.utils.mongo.operation_locks import (
    DEFAULT_JOB_HISTORY_TTL_SECONDS, start_background_job)

DEFAULT_QUEUE_NAME = "default"
DEFAULT_JOB_TIMEOUT_SECONDS = 60 * 60 * 2  # 2 hours; individual jobs can override

_redis_connection = None
_queue = None
_worker_app = None


def get_redis_connection():
    global _redis_connection
    if _redis_connection is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_connection = redis.from_url(redis_url)
    return _redis_connection


def get_queue():
    global _queue
    if _queue is None:
        _queue = Queue(DEFAULT_QUEUE_NAME, connection=get_redis_connection())
    return _queue


def reset_job_queue_state_for_tests():
    """Test-only hook: force get_redis_connection/get_queue to rebuild next call."""
    global _redis_connection, _queue
    _redis_connection = None
    _queue = None


def get_worker_app():
    """Build (once per process) the Flask app instance background tasks run against.

    Only ever called from within the worker process, never from the web
    process, so the scheduler must stay disabled here (it already runs in
    the web process) to avoid double-firing cron jobs.
    """
    global _worker_app
    if _worker_app is None:
        os.environ.setdefault("ENABLE_SCHEDULER", "0")
        from flymanager.app import create_app
        _worker_app = create_app()
    return _worker_app


def enqueue_job(
    db,
    *,
    key,
    actor,
    label,
    func,
    args=(),
    kwargs=None,
    ttl_seconds=DEFAULT_JOB_HISTORY_TTL_SECONDS,
    job_timeout=DEFAULT_JOB_TIMEOUT_SECONDS,
    metadata=None,
    conflict_message=None,
):
    """Record the job as queued and hand it to the worker.

    Raises OperationLockConflict (same as the old hold_operation_lock) if a
    job with this key is already queued or running. The returned key is both
    the Mongo job-status document's key and the RQ job id, so one string
    threads through enqueue -> status polling -> jobs history.
    """
    job_key = start_background_job(
        db,
        key=key,
        actor=actor,
        label=label,
        ttl_seconds=ttl_seconds,
        metadata=metadata,
        conflict_message=conflict_message,
    )
    get_queue().enqueue(
        func,
        *args,
        kwargs=kwargs or {},
        job_id=job_key,
        job_timeout=job_timeout,
        result_ttl=0,
        failure_ttl=3600,
    )
    return job_key
