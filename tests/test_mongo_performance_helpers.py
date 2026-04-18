from unittest.mock import patch

from flymanager.app import create_app
from flymanager.utils.mongo.db import ensure_mongo_indexes
from flymanager.utils.mongo.helpers import (add_metadata, clear_metadata_cache,
                                            get_metadata,
                                            preload_metadata_cache)


class _FakeFindCollection:
    def __init__(self, documents):
        self.documents = list(documents)
        self.find_calls = 0

    def find(self):
        self.find_calls += 1
        return list(self.documents)

    def find_one(self, selector):
        value = selector.get("Value")
        for document in self.documents:
            if document.get("Value") == value:
                return dict(document)
        return None

    def insert_one(self, document):
        self.documents.append(dict(document))


class _FakeIndexCollection:
    def __init__(self):
        self.calls = []

    def create_index(self, keys, name=None):
        self.calls.append({"keys": list(keys), "name": name})


class _FakeDatabase(dict):
    def __init__(self, name, collections):
        super().__init__(collections)
        self.name = name


def _settings_payload():
    return {
        "lab_info": {
            "lab_name": "Test Lab",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
        "theme": {
            "accent_color": "#0055aa",
            "dark_mode": False,
        },
    }


def test_get_metadata_uses_cache_until_invalidated():
    clear_metadata_cache()
    collection = _FakeFindCollection([{"Value": "Balancer"}])
    db = _FakeDatabase("unit-test", {"types": collection})

    first = get_metadata("types", db)
    second = get_metadata("types", db)

    assert first == ["Balancer"]
    assert second == ["Balancer"]
    assert collection.find_calls == 1

    add_metadata("types", "Gal4", db)
    third = get_metadata("types", db)

    assert third == ["Balancer", "Gal4"]
    assert collection.find_calls == 2
    clear_metadata_cache()


def test_ensure_mongo_indexes_creates_access_indexes():
    stocks = _FakeIndexCollection()
    crosses = _FakeIndexCollection()
    db = _FakeDatabase("unit-test", {"stocks": stocks, "crosses": crosses})

    ensure_mongo_indexes(db)

    assert stocks.calls == [
        {"keys": [("User", 1), ("UniqueID", 1)], "name": "stocks_user_uid"},
        {"keys": [("AssignedTo", 1), ("UniqueID", 1)], "name": "stocks_assigned_uid"},
    ]
    assert crosses.calls == [
        {"keys": [("User", 1), ("UniqueID", 1)], "name": "crosses_user_uid"},
        {"keys": [("AssignedTo", 1), ("UniqueID", 1)], "name": "crosses_assigned_uid"},
    ]


def test_preload_metadata_cache_warms_selected_metadata_types():
    clear_metadata_cache()
    types_collection = _FakeFindCollection([{"Value": "Balancer"}])
    species_collection = _FakeFindCollection([{"Value": "D. melanogaster"}])
    db = _FakeDatabase(
        "unit-test",
        {
            "types": types_collection,
            "species": species_collection,
        },
    )

    warmed = preload_metadata_cache(db, metadata_types=("types", "species"))

    assert warmed == {
        "types": ["Balancer"],
        "species": ["D. melanogaster"],
    }
    assert types_collection.find_calls == 1
    assert species_collection.find_calls == 1

    # Verify the preload populated the cache.
    assert get_metadata("types", db) == ["Balancer"]
    assert get_metadata("species", db) == ["D. melanogaster"]
    assert types_collection.find_calls == 1
    assert species_collection.find_calls == 1
    clear_metadata_cache()


def test_create_app_preloads_page_caches_when_enabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)
    monkeypatch.delenv("WARM_PAGE_CACHES_ON_STARTUP", raising=False)

    with patch("flymanager.app.get_settings", return_value=_settings_payload()), patch(
        "flymanager.app.preload_metadata_cache"
    ) as preload_metadata, patch(
        "flymanager.app.preload_flybase_stock_indexes"
    ) as preload_flybase:
        app = create_app()

    assert app is not None
    preload_metadata.assert_called_once()
    preload_flybase.assert_called_once_with()


def test_create_app_skips_page_cache_preload_when_disabled(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)
    monkeypatch.setenv("WARM_PAGE_CACHES_ON_STARTUP", "0")

    with patch("flymanager.app.get_settings", return_value=_settings_payload()), patch(
        "flymanager.app.preload_metadata_cache"
    ) as preload_metadata, patch(
        "flymanager.app.preload_flybase_stock_indexes"
    ) as preload_flybase:
        app = create_app()

    assert app is not None
    preload_metadata.assert_not_called()
    preload_flybase.assert_not_called()