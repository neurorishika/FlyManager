from datetime import datetime

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.cache_force_refresh import (
    CACHE_FORCE_REFRESH_PHRASES, check_force_refresh_cooldown, record_force_refresh)


def test_check_cooldown_allows_first_ever_run():
    db = FakeDatabase()
    allowed, retry_after = check_force_refresh_cooldown("phenotype", db)
    assert allowed is True
    assert retry_after is None


def test_record_then_check_blocks_within_24_hours():
    db = FakeDatabase()
    record_force_refresh("phenotype", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    allowed, retry_after = check_force_refresh_cooldown(
        "phenotype", db, now=datetime(2026, 8, 14, 11, 0),
    )
    assert allowed is False
    assert retry_after == "2026-08-15 10:00"


def test_cooldown_expires_after_24_hours():
    db = FakeDatabase()
    record_force_refresh("phenotype", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    allowed, retry_after = check_force_refresh_cooldown(
        "phenotype", db, now=datetime(2026, 8, 15, 10, 1),
    )
    assert allowed is True
    assert retry_after is None


def test_cooldowns_are_independent_per_cache():
    db = FakeDatabase()
    record_force_refresh("phenotype", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    allowed, _ = check_force_refresh_cooldown(
        "standardization", db, now=datetime(2026, 8, 14, 10, 0),
    )
    assert allowed is True


def test_record_force_refresh_stores_username_and_timestamp():
    db = FakeDatabase()
    record_force_refresh("provider_match", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    from flymanager.utils.mongo.settings import get_settings
    entry = get_settings(db)["cacheForceRefresh"]["provider_match"]
    assert entry == {"lastRun": "2026-08-14 10:00", "byUser": "admin"}


def test_phrases_are_distinct_per_cache():
    assert len(set(CACHE_FORCE_REFRESH_PHRASES.values())) == len(CACHE_FORCE_REFRESH_PHRASES)
    assert set(CACHE_FORCE_REFRESH_PHRASES.keys()) == {"phenotype", "standardization", "provider_match"}
