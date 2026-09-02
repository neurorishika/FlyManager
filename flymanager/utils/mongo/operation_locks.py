from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo.errors import DuplicateKeyError


class OperationLockConflict(RuntimeError):
    """Raised when another request already holds an operation lock."""


def _utcnow():
    return datetime.now(timezone.utc)


def _normalize_lock_keys(keys):
    normalized = []
    seen = set()

    for key in keys:
        text = str(key or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)

    normalized.sort()
    return normalized


def _cleanup_expired_locks(collection):
    collection.delete_many({"expires_at": {"$lte": _utcnow()}})


def _build_conflict_message(existing_lock, fallback_message):
    if fallback_message:
        return fallback_message

    label = str(existing_lock.get("label") or "Another request")
    actor = str(existing_lock.get("actor") or "another user")
    return f"{label} is already running for {actor}. Please wait for it to finish and try again."


class _InMemoryOperationLockCollection:
    def __init__(self):
        self._documents = []

    def insert_one(self, document):
        key = document.get("key")
        if any(existing.get("key") == key for existing in self._documents):
            raise DuplicateKeyError(f"Duplicate operation lock for key {key}")
        self._documents.append(dict(document))

    def find_one(self, query, projection=None):
        del projection
        for document in self._documents:
            if self._matches(document, query):
                return dict(document)
        return None

    def find(self, query=None, projection=None):
        del projection
        return [
            dict(document)
            for document in self._documents
            if self._matches(document, query or {})
        ]

    def update_one(self, query, update):
        for document in self._documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))
                return
        raise KeyError("No matching operation lock document to update")

    def delete_many(self, query):
        self._documents = [
            document for document in self._documents
            if not self._matches(document, query)
        ]

    @staticmethod
    def _matches(document, query):
        for key, value in query.items():
            document_value = document.get(key)
            if isinstance(value, dict):
                if "$lte" in value and not (document_value <= value["$lte"]):
                    return False
                if "$gt" in value and not (document_value > value["$gt"]):
                    return False
                if "$ne" in value and document_value == value["$ne"]:
                    return False
                if "$in" in value and document_value not in value["$in"]:
                    return False
                continue
            if document_value != value:
                return False
        return True


def _get_operation_lock_collection(db):
    try:
        return db["operation_locks"]
    except KeyError:
        if hasattr(db, "setdefault"):
            return db.setdefault("operation_locks", _InMemoryOperationLockCollection())
        raise


@contextmanager
def hold_operation_lock(
    db,
    *,
    key,
    actor,
    label,
    ttl_seconds=900,
    metadata=None,
    conflict_message=None,
):
    with hold_operation_locks(
        db,
        keys=[key],
        actor=actor,
        label=label,
        ttl_seconds=ttl_seconds,
        metadata=metadata,
        conflict_message=conflict_message,
    ):
        yield


@contextmanager
def hold_operation_locks(
    db,
    *,
    keys,
    actor,
    label,
    ttl_seconds=900,
    metadata=None,
    conflict_message=None,
):
    normalized_keys = _normalize_lock_keys(keys)
    if not normalized_keys:
        yield
        return

    collection = _get_operation_lock_collection(db)
    _cleanup_expired_locks(collection)
    token = uuid4().hex
    now = _utcnow()
    expires_at = now + timedelta(seconds=max(int(ttl_seconds), 1))
    acquired_keys = []

    try:
        for key in normalized_keys:
            try:
                collection.insert_one(
                    {
                        "key": key,
                        "token": token,
                        "actor": actor,
                        "label": label,
                        "metadata": metadata or {},
                        "created_at": now,
                        "expires_at": expires_at,
                    }
                )
                acquired_keys.append(key)
            except DuplicateKeyError:
                existing_lock = collection.find_one(
                    {"key": key},
                    {"label": 1, "actor": 1, "expires_at": 1},
                ) or {}
                raise OperationLockConflict(
                    _build_conflict_message(existing_lock, conflict_message)
                )

        yield
    finally:
        if acquired_keys:
            collection.delete_many({"token": token})


def record_operation_lock_keys(unique_ids):
    return [f"record-mutation:{uid}" for uid in _normalize_lock_keys(unique_ids)]


# --- Background job tracking -------------------------------------------------
#
# A background job is just an operation lock document that isn't deleted the
# moment the work finishes. The same "key" that used to be a pure mutex now
# doubles as a job-status record: created with status="queued" when the route
# enqueues it, updated to "running"/"succeeded"/"failed" by the worker, and
# left in place (browsable as job history) until its TTL expires and the
# normal _cleanup_expired_locks sweep removes it.

JOB_STATUS_QUEUED = "queued"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"

_ACTIVE_JOB_STATUSES = (JOB_STATUS_QUEUED, JOB_STATUS_RUNNING)

DEFAULT_JOB_HISTORY_TTL_SECONDS = 60 * 60 * 48  # 48 hours


def start_background_job(
    db,
    *,
    key,
    actor,
    label,
    ttl_seconds=DEFAULT_JOB_HISTORY_TTL_SECONDS,
    metadata=None,
    conflict_message=None,
):
    """Create a queued job record, or raise OperationLockConflict if one is already active.

    Returns the job's key (equal to the RQ job id the caller should use), so
    the same string threads through the queue, the status document, and the
    polling endpoint.
    """
    normalized_keys = _normalize_lock_keys([key])
    if not normalized_keys:
        raise ValueError("A non-empty job key is required")
    job_key = normalized_keys[0]

    collection = _get_operation_lock_collection(db)
    _cleanup_expired_locks(collection)

    existing = collection.find_one({"key": job_key})
    if existing and (
        existing.get("status") in _ACTIVE_JOB_STATUSES
        or "status" not in existing
    ):
        # A record with no "status" field is a plain hold_operation_lock(s)
        # mutex (e.g. a cron job's distributed lock), not a finished job
        # record - it's still actively held and must block, the same as an
        # active queued/running job would.
        raise OperationLockConflict(
            _build_conflict_message(existing, conflict_message)
        )

    # A finished job record can still be sitting here (kept for history); a
    # fresh run replaces it outright rather than erroring on the unique key.
    if existing:
        collection.delete_many({"key": job_key})

    now = _utcnow()
    collection.insert_one(
        {
            "key": job_key,
            "token": uuid4().hex,
            "actor": actor,
            "label": label,
            "metadata": metadata or {},
            "status": JOB_STATUS_QUEUED,
            "progress": None,
            "result": None,
            "error": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "expires_at": now + timedelta(seconds=max(int(ttl_seconds), 1)),
        }
    )
    return job_key


def mark_job_running(db, key):
    _get_operation_lock_collection(db).update_one(
        {"key": key},
        {"$set": {"status": JOB_STATUS_RUNNING, "started_at": _utcnow()}},
    )


def update_job_progress(db, key, *, current=None, total=None, message=None):
    _get_operation_lock_collection(db).update_one(
        {"key": key},
        {
            "$set": {
                "progress": {
                    "current": current,
                    "total": total,
                    "message": message,
                }
            }
        },
    )


def mark_job_succeeded(db, key, *, result=None):
    _get_operation_lock_collection(db).update_one(
        {"key": key},
        {
            "$set": {
                "status": JOB_STATUS_SUCCEEDED,
                "result": result,
                "error": None,
                "finished_at": _utcnow(),
            }
        },
    )


def mark_job_failed(db, key, *, error):
    _get_operation_lock_collection(db).update_one(
        {"key": key},
        {
            "$set": {
                "status": JOB_STATUS_FAILED,
                "error": str(error),
                "finished_at": _utcnow(),
            }
        },
    )


def get_job_status(db, key):
    collection = _get_operation_lock_collection(db)
    document = collection.find_one({"key": key, "expires_at": {"$gt": _utcnow()}})
    if not document or "status" not in document:
        return None
    return document


def list_recent_jobs(db, *, actor=None, limit=50):
    collection = _get_operation_lock_collection(db)
    query = {
        "status": {
            "$in": list(_ACTIVE_JOB_STATUSES + (JOB_STATUS_SUCCEEDED, JOB_STATUS_FAILED))
        },
        "expires_at": {"$gt": _utcnow()},
    }
    if actor:
        query["actor"] = actor
    # This endpoint is polled by every signed-in browser. Let Mongo use its
    # TTL index for expiry and do ordering/limiting server-side; polling must
    # remain read-only rather than issuing a delete sweep every few seconds.
    cursor = collection.find(query)
    if isinstance(cursor, list):
        cursor.sort(key=lambda doc: doc.get("created_at") or _utcnow(), reverse=True)
        return cursor[:limit]
    return list(cursor.sort("created_at", -1).limit(limit))
