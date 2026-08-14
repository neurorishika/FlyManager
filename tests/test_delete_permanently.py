import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo_records import delete_owned_documents_if_status


def _stocks_db():
    return FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "No longer maintained"},
            {"UniqueID": "s2", "User": "alice", "Status": "Healthy"},
            {"UniqueID": "s3", "User": "alice", "Status": "No longer maintained"},
            {"UniqueID": "s4", "User": "bob", "Status": "No longer maintained"},
        ]
    })


def test_deletes_only_matching_status_and_owner():
    db = _stocks_db()
    deleted, skipped = delete_owned_documents_if_status(
        "stocks", "alice", ["s1", "s2", "s3", "s4", "missing"], db,
        required_status="No longer maintained",
    )
    assert sorted(deleted) == ["s1", "s3"]
    assert sorted(skipped) == ["missing", "s2", "s4"]
    remaining = {doc["UniqueID"] for doc in db["stocks"].find({})}
    assert remaining == {"s2", "s4"}


def test_empty_uids_returns_empty_without_querying():
    db = _stocks_db()
    deleted, skipped = delete_owned_documents_if_status(
        "stocks", "alice", [], db, required_status="No longer maintained",
    )
    assert deleted == [] and skipped == []


def test_issues_one_find_and_one_delete_many_regardless_of_count(monkeypatch):
    db = _stocks_db()
    collection = db["stocks"]
    counts = {"find": 0, "delete_many": 0, "find_one": 0}
    for method in counts:
        original = getattr(collection.__class__, method)

        def make_spy(name, orig):
            def spy(self, *args, **kwargs):
                counts[name] += 1
                return orig(self, *args, **kwargs)
            return spy

        # monkeypatch.setattr on the class (shared across every test file
        # that imports tests.mongo_fakes) auto-restores the original method
        # after this test, unlike a manual setattr with no matching restore.
        monkeypatch.setattr(collection.__class__, method, make_spy(method, original))

    delete_owned_documents_if_status(
        "stocks", "alice", ["s1", "s2", "s3"], db, required_status="No longer maintained",
    )

    assert counts["find_one"] == 0
    assert counts["find"] == 1
    assert counts["delete_many"] == 1
