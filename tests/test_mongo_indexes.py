import flymanager.app  # noqa: F401

from unittest.mock import patch

from tests.mongo_fakes import FakeCollection, FakeDatabase
from flymanager.utils.mongo.db import ensure_mongo_indexes


def test_ensure_mongo_indexes_creates_cross_parent_lookup_indexes():
    db = FakeDatabase()
    calls = []
    original_create_index = FakeCollection.create_index

    def recording_create_index(self, *args, **kwargs):
        calls.append((self.name, args, kwargs))
        return original_create_index(self, *args, **kwargs)

    with patch.object(FakeCollection, "create_index", recording_create_index):
        ensure_mongo_indexes(db)

    assert (
        "crosses",
        ([("User", 1), ("MaleUniqueID", 1)],),
        {"name": "crosses_user_male_uid"},
    ) in calls
    assert (
        "crosses",
        ([("User", 1), ("FemaleUniqueID", 1)],),
        {"name": "crosses_user_female_uid"},
    ) in calls
