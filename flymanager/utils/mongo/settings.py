import copy
import os
import threading
import time

from pymongo.database import Database

DEFAULT_SETTINGS = {
    'theme': {
        'dark_mode': False,
        'accent_color': '#007bff'
    },
    'lab_info': {
        'lab_name': 'Ruta Lab',
        'admin_name': 'Lab Administrator',
        'admin_email': 'admin@example.com'
    }
}

_SETTINGS_CACHE = {}
_SETTINGS_CACHE_LOCK = threading.Lock()


def _cache_ttl_seconds():
    return max(0.0, float(os.getenv("SETTINGS_CACHE_TTL_SECONDS", "5")))


def _cache_get(db):
    with _SETTINGS_CACHE_LOCK:
        entry = _SETTINGS_CACHE.get(id(db))
        if entry and entry[0] is db and entry[1] > time.monotonic():
            return copy.deepcopy(entry[2])
    return None


def _cache_set(db, settings):
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE[id(db)] = (
            db,
            time.monotonic() + _cache_ttl_seconds(),
            copy.deepcopy(settings),
        )


def _cache_invalidate(db):
    with _SETTINGS_CACHE_LOCK:
        _SETTINGS_CACHE.pop(id(db), None)


def get_settings(db: Database) -> dict:
    """Get application settings, filling in any missing defaults.

    The singleton is not owned solely by this module: revision counters
    (markerCatalogRevision, markerImageRevision) are bumped with
    find_one_and_update(..., upsert=True), which on an empty database
    creates a document holding only that counter. Initializing defaults
    only when the document is entirely absent left such a skeleton in
    place forever, and every template that reads settings.lab_info then
    raised UndefinedError on every page. So merge missing top-level keys
    rather than testing for the document's existence.
    """
    cached = _cache_get(db)
    if cached is not None:
        return cached

    settings = db['settings'].find_one({})
    if not settings:
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        db['settings'].insert_one(settings)
        _cache_set(db, settings)
        return settings

    missing = {
        key: value
        for key, value in DEFAULT_SETTINGS.items()
        if key not in settings
    }
    if missing:
        # Addressed by {} like every other write to this singleton, rather
        # than by _id: the collection holds exactly one document, and this
        # does not assume the caller's copy carries an _id.
        db['settings'].update_one({}, {'$set': missing})
        settings.update(missing)
    _cache_set(db, settings)
    return settings

def update_settings(updates: dict, db: Database) -> bool:
    """Update application settings in MongoDB"""
    try:
        result = db['settings'].update_one(
            {}, 
            {'$set': updates},
            upsert=True
        )
        _cache_invalidate(db)
        return bool(result.modified_count > 0 or result.upserted_id)
    except Exception as e:
        print(f"Error updating settings: {e}")
        return False
