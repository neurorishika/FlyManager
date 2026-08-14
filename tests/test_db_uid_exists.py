import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.db import uid_exists


def test_uid_exists_true_for_stock():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice"}]})
    assert uid_exists("s1", db) is True


def test_uid_exists_true_for_cross():
    db = FakeDatabase({"crosses": [{"UniqueID": "c1", "User": "alice"}]})
    assert uid_exists("c1", db) is True


def test_uid_exists_false_when_absent():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice"}]})
    assert uid_exists("nope", db) is False


def test_uid_exists_only_requests_id_field(monkeypatch):
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice"}]})
    seen_projections = []
    original_find_one = db["stocks"].__class__.find_one

    def spy(self, query=None, projection=None):
        seen_projections.append(projection)
        return original_find_one(self, query, projection)

    monkeypatch.setattr(db["stocks"].__class__, "find_one", spy)
    uid_exists("s1", db)
    assert all(projection == {"_id": 1} for projection in seen_projections)
