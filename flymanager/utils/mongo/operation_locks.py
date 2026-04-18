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