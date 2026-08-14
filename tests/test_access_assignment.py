import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.access import (
    update_document_assignment, bulk_update_document_assignments,
)


def _db_with_reports():
    return FakeDatabase({
        "users": [
            {"Username": "alice", "ReportsTo": ""},
            {"Username": "bob", "ReportsTo": "alice"},
        ],
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "AssignedTo": "", "TrayID": "T1", "ModificationLog": ""},
            {"UniqueID": "s2", "User": "alice", "AssignedTo": "", "TrayID": "T1", "ModificationLog": ""},
        ],
        "crosses": [
            {"UniqueID": "c1", "User": "alice", "AssignedTo": "", "TrayID": "T1", "ModificationLog": ""},
        ],
    })


def test_bulk_matches_per_item_path_for_identical_inputs():
    single_db = _db_with_reports()
    bulk_db = _db_with_reports()
    targets = [("stocks", "s1"), ("stocks", "s2"), ("crosses", "c1")]

    for collection_name, uid in targets:
        update_document_assignment(collection_name, "alice", uid, "bob", single_db)

    bulk_update_document_assignments("alice", "bob", targets, bulk_db)

    for collection_name, uid in targets:
        single_doc = single_db[collection_name].find_one({"UniqueID": uid})
        bulk_doc = bulk_db[collection_name].find_one({"UniqueID": uid})
        assert single_doc["AssignedTo"] == bulk_doc["AssignedTo"] == "bob"
        assert single_doc["ModificationLog"].split(" : ", 1)[1] == bulk_doc["ModificationLog"].split(" : ", 1)[1]


def test_bulk_rejects_non_direct_report_without_writing():
    db = _db_with_reports()
    updated_count, error = bulk_update_document_assignments(
        "alice", "carol", [("stocks", "s1")], db,
    )
    assert updated_count == 0
    assert error == "Assignee must be one of your direct reports."
    assert db["stocks"].find_one({"UniqueID": "s1"})["AssignedTo"] == ""


def test_bulk_issues_one_find_and_one_bulk_write_per_collection():
    db = _db_with_reports()
    counts = {"stocks_find": 0, "stocks_bulk_write": 0, "crosses_find": 0, "crosses_bulk_write": 0}

    # FakeCollection is a single shared class across all collection names
    # (db["stocks"] and db["crosses"] are distinct instances of the *same*
    # class), so patching `__class__.find`/`__class__.bulk_write` once per
    # collection name would chain the spies together and double-count every
    # call (see tests/test_tray_occupancy_bulk.py for the same gotcha).
    # Patch each method exactly once and dispatch on `self.name` instead.
    collection_class = db["stocks"].__class__
    original_find = collection_class.find
    original_bulk_write = collection_class.bulk_write

    def find_spy(self, *args, **kwargs):
        key = f"{self.name}_find"
        if key in counts:
            counts[key] += 1
        return original_find(self, *args, **kwargs)

    def bulk_write_spy(self, *args, **kwargs):
        key = f"{self.name}_bulk_write"
        if key in counts:
            counts[key] += 1
        return original_bulk_write(self, *args, **kwargs)

    collection_class.find = find_spy
    collection_class.bulk_write = bulk_write_spy
    try:
        bulk_update_document_assignments(
            "alice", "bob", [("stocks", "s1"), ("stocks", "s2"), ("crosses", "c1")], db,
        )
    finally:
        collection_class.find = original_find
        collection_class.bulk_write = original_bulk_write

    assert counts["stocks_find"] == 1
    assert counts["stocks_bulk_write"] == 1
    assert counts["crosses_find"] == 1
    assert counts["crosses_bulk_write"] == 1
