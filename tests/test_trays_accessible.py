import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.trays import get_accessible_trays


def _db_with_shared_tray():
    return FakeDatabase({
        "trays": [
            {"UniqueID": "tray-bob-T1", "User": "bob", "TrayID": "T1", "Rows": 10, "Columns": 10},
        ],
        "stocks": [
            {"UniqueID": "s1", "User": "bob", "AssignedTo": "alice", "TrayID": "T1"},
        ],
        "crosses": [],
    })


def test_get_accessible_trays_backfills_shared_owner_tray():
    db = _db_with_shared_tray()
    trays = get_accessible_trays("alice", db)
    assert [t["UniqueID"] for t in trays] == ["tray-bob-T1"]


def test_get_accessible_trays_issues_one_batched_backfill_query():
    db = _db_with_shared_tray()
    collection = db["trays"]
    original_find = collection.__class__.find
    original_find_one = collection.__class__.find_one
    find_one_calls = []

    def spy_find_one(self, *args, **kwargs):
        find_one_calls.append((args, kwargs))
        return original_find_one(self, *args, **kwargs)

    collection.__class__.find_one = spy_find_one
    try:
        get_accessible_trays("alice", db)
    finally:
        collection.__class__.find_one = original_find_one

    assert len(find_one_calls) == 0, (
        "backfill must use one batched find(), not a find_one() per accessible record"
    )
