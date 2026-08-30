import logging
import threading
import time

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)
_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()
_SNAPSHOT = {"entries": [], "by_marker_key": {}, "revision": -1}
_LAST_PROBE = 0.0
_PROBE_LOCK = threading.Lock()
SETTINGS_FIELD = "markerImageRevision"


def _entry_sort_key(entry):
    return (int((entry.get("display") or {}).get("sortOrder") or 0),
            str(entry.get("imageId") or ""))


def compile_image_catalog(documents, revision=0):
    entries = [d for d in documents or [] if d.get("imageId") and d.get("storageId")]
    entries.sort(key=_entry_sort_key)
    by_marker_key = {}
    for entry in entries:
        for key in (entry.get("match") or {}).get("markerKeys") or []:
            by_marker_key.setdefault(str(key), []).append(entry)
    return {"entries": entries, "by_marker_key": by_marker_key,
            "revision": int(revision or 0)}


def get_image_catalog():
    """Return the installed process-global snapshot without database access."""
    return _SNAPSHOT


def set_image_catalog_for_testing(snapshot):
    global _SNAPSHOT
    with _LOCK:
        _SNAPSHOT = snapshot


def read_image_revision(db):
    return int((db["settings"].find_one({}) or {}).get(SETTINGS_FIELD) or 0)


def bump_image_revision(db):
    result = db["settings"].find_one_and_update(
        {}, {"$inc": {SETTINGS_FIELD: 1}}, upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not isinstance(result, dict) or SETTINGS_FIELD not in result:
        raise RuntimeError(f"Failed to bump {SETTINGS_FIELD}: {result!r}")
    return int(result[SETTINGS_FIELD])


def refresh_image_catalog(db, *, force=False):
    global _SNAPSHOT
    with _REFRESH_LOCK:
        revision = read_image_revision(db)
        if not force and revision == _SNAPSHOT.get("revision"):
            return _SNAPSHOT
        snapshot = compile_image_catalog(list(db["marker_images"].find({})), revision)
        with _LOCK:
            _SNAPSHOT = snapshot
        return snapshot


def maybe_refresh_image_catalog(db, *, interval_seconds=30):
    global _LAST_PROBE
    now = time.monotonic()
    with _PROBE_LOCK:
        if now - _LAST_PROBE < interval_seconds:
            return _SNAPSHOT
        _LAST_PROBE = now
    return refresh_image_catalog(db)
