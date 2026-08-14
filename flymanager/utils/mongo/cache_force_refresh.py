from datetime import datetime, timedelta

from flymanager.utils.mongo.settings import get_settings, update_settings

CACHE_FORCE_REFRESH_COOLDOWN_HOURS = 24

CACHE_FORCE_REFRESH_PHRASES = {
    "phenotype": "FORCE RECOMPUTE PHENOTYPE",
    "standardization": "FORCE RECOMPUTE STANDARDIZATION",
    "provider_match": "FORCE RECOMPUTE PROVIDER MATCH",
}

_TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")


def _parse_timestamp(text):
    normalized = str(text or "").strip()
    if not normalized:
        return None
    for timestamp_format in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(normalized, timestamp_format)
        except ValueError:
            continue
    return None


def check_force_refresh_cooldown(cache_key, db, *, now=None):
    """Returns (allowed, retry_after). retry_after is None when allowed."""
    now = now or datetime.now()
    settings = get_settings(db)
    entry = (settings.get("cacheForceRefresh") or {}).get(cache_key) or {}
    last_run = _parse_timestamp(entry.get("lastRun"))
    if last_run is None:
        return True, None

    cooldown_ends = last_run + timedelta(hours=CACHE_FORCE_REFRESH_COOLDOWN_HOURS)
    if now >= cooldown_ends:
        return True, None

    return False, cooldown_ends.strftime("%Y-%m-%d %H:%M")


def record_force_refresh(cache_key, username, db, *, now=None):
    now = now or datetime.now()
    settings = get_settings(db)
    cache_force_refresh = dict(settings.get("cacheForceRefresh") or {})
    cache_force_refresh[cache_key] = {
        "lastRun": now.strftime("%Y-%m-%d %H:%M"),
        "byUser": username,
    }
    update_settings({"cacheForceRefresh": cache_force_refresh}, db)
