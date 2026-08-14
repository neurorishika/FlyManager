"""Equivalence tests for the batched bulk operations.

The batched flip / status-change paths must produce byte-for-byte identical
documents to the original per-item path (flip_stock / flip_cross / edit_*),
just with far fewer database round-trips. These tests run both paths against
identical in-memory fixtures and assert the resulting collections match.
"""
import copy
import datetime
from types import SimpleNamespace
from unittest.mock import patch

from pymongo import ReturnDocument

# Import the app package first so flymanager.utils.mongo is fully initialised
# before we pull submodules from it (avoids a partial-init circular import when
# a mongo submodule is the entry point rather than flymanager.app).
import flymanager.app  # noqa: F401

from flymanager.utils.mongo.bulk_operations import (
    bulk_change_status_records, bulk_flip_records)
from flymanager.utils.mongo.crosses import edit_cross, flip_cross
from flymanager.utils.mongo.stocks import edit_stock, flip_stock


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    @property
    def _records(self):
        return self.database.data.setdefault(self.name, [])

    @staticmethod
    def _matches(record, query):
        for key, value in query.items():
            if isinstance(value, dict) and "$in" in value:
                if record.get(key) not in value["$in"]:
                    return False
            elif record.get(key) != value:
                return False
        return True

    def find_one(self, query):
        for record in self._records:
            if self._matches(record, query):
                return dict(record)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return [dict(record) for record in self._records if self._matches(record, query)]

    def update_one(self, query, update):
        for record in self._records:
            if self._matches(record, query):
                record.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find_one_and_update(self, query, update, return_document="AFTER"):
        # `return_document` may be the literal string "AFTER" or the real
        # pymongo.ReturnDocument.AFTER enum member; check both forms.
        return_after = return_document in ("AFTER", ReturnDocument.AFTER)
        for record in self._records:
            if self._matches(record, query):
                if not return_after:
                    before = dict(record)
                    record.update(update.get("$set", {}))
                    return before
                record.update(update.get("$set", {}))
                return dict(record)
        return None

    def insert_one(self, document):
        self._records.append(dict(document))
        return SimpleNamespace(inserted_id=len(self._records))

    def insert_many(self, documents):
        documents = list(documents)
        for document in documents:
            self._records.append(dict(document))
        return SimpleNamespace(inserted_ids=list(range(len(documents))))

    def bulk_write(self, operations, ordered=True):
        modified = 0
        for operation in operations:
            query = operation._filter
            update = operation._doc
            for record in self._records:
                if self._matches(record, query):
                    record.update(update.get("$set", {}))
                    modified += 1
                    break
        return SimpleNamespace(modified_count=modified)


class FakeDatabase:
    def __init__(self, initial_data=None):
        self.data = {
            name: [dict(item) for item in records]
            for name, records in (initial_data or {}).items()
        }

    def __getitem__(self, name):
        self.data.setdefault(name, [])
        return FakeCollection(self, name)

    def __getattr__(self, name):
        if name == "data":
            raise AttributeError(name)
        self.__dict__.setdefault("data", {}).setdefault(name, [])
        return FakeCollection(self, name)


USER = "scientist"
# The route validates flipTime into a datetime and the background task forwards
# its .isoformat() string to the batch function, so the timestamp both paths
# actually receive in production is an ISO string - mirror that here.
FLIP_TIME = datetime.datetime(2026, 2, 1, 9, 0).isoformat()


def make_stock(uid, **overrides):
    stock = {
        "UniqueID": uid,
        "User": USER,
        "Genotype": "w[1118]",
        "Name": uid,
        "SourceID": "S1",
        "StockSource": "BDSC",
        "Species": "D. melanogaster",
        "SeriesID": "1",
        "ReplicateID": "1",
        "Type": "WT",
        "Status": "Healthy",
        "FoodType": "Molasses",
        "VialLifetime": "28",
        "FlipFrequency": "7",
        "DevelopmentalTime": "10",
        "Provenance": "Unknown",
        "Comments": "",
        "ModificationLog": "created",
        "FlipLog": "V2, 2026-01-08 10:00; V1, 2026-01-01 10:00",
        "CurrentlyAliveVials": "V1, V2",
        "LastFlipDate": "2026-01-08 10:00",
        "TrayID": "",
        "TrayPosition": "",
    }
    stock.update(overrides)
    return stock


def make_cross(uid, **overrides):
    cross = {
        "UniqueID": uid,
        "User": USER,
        "MaleGenotype": "w[1118]",
        "FemaleGenotype": "w[1118]",
        "Name": uid,
        "MaleSpecies": "D. melanogaster",
        "FemaleSpecies": "D. melanogaster",
        "Status": "Healthy",
        "FoodType": "Molasses",
        "VialLifetime": "14",
        "FlipFrequency": "7",
        "DevelopmentalTime": "10",
        "MaxCrossLifetime": "21",
        "Comments": "",
        "ModificationLog": "created",
        "FlipLog": "V2, 2026-01-08 10:00; V1, 2026-01-01 10:00",
        "CurrentlyAliveVials": "V1, V2",
        "LastFlipDate": "2026-01-08 10:00",
        "TrayID": "",
        "TrayPosition": "",
    }
    cross.update(overrides)
    return cross


def _sequential_flip(db, uids, *, new_status=None, comment=""):
    for uid in uids:
        stock = db["stocks"].find_one({"UniqueID": uid, "User": USER})
        if stock:
            flip_stock(stock["User"], uid, db, FLIP_TIME, new_status=new_status,
                       added_comment=comment)
            continue
        cross = db["crosses"].find_one({"UniqueID": uid, "User": USER})
        if cross:
            flip_cross(cross["User"], uid, db, FLIP_TIME, new_status=new_status,
                       added_comment=comment)


def _run_both(initial, uids, **flip_kwargs):
    sequential_db = FakeDatabase(copy.deepcopy(initial))
    batched_db = FakeDatabase(copy.deepcopy(initial))
    _sequential_flip(sequential_db, uids, **flip_kwargs)
    results = bulk_flip_records(USER, uids, batched_db, FLIP_TIME, **flip_kwargs)
    return sequential_db, batched_db, results


def test_bulk_flip_matches_sequential_plain():
    initial = {
        "stocks": [make_stock("stock-1"), make_stock("stock-2")],
        "crosses": [make_cross("cross-1")],
    }
    uids = ["stock-1", "cross-1", "stock-2"]

    sequential_db, batched_db, results = _run_both(initial, uids)

    assert sequential_db.data["stocks"] == batched_db.data["stocks"]
    assert sequential_db.data["crosses"] == batched_db.data["crosses"]
    assert {entry["uid"] for entry in results["success"]} == set(uids)
    assert results["failed"] == []


def test_bulk_flip_matches_sequential_with_status_and_comment():
    initial = {
        "stocks": [make_stock("stock-1")],
        "crosses": [make_cross("cross-1")],
    }
    uids = ["stock-1", "cross-1"]

    sequential_db, batched_db, results = _run_both(
        initial, uids, new_status="Showing Issues", comment="check vials"
    )

    assert sequential_db.data["stocks"] == batched_db.data["stocks"]
    assert sequential_db.data["crosses"] == batched_db.data["crosses"]
    # sanity: the flip actually changed state
    flipped = batched_db.data["stocks"][0]
    assert flipped["Status"] == "Showing Issues"
    assert flipped["LastFlipDate"] == "2026-02-01 09:00:00"
    assert "check vials" in flipped["Comments"]
    assert results["failed"] == []


def test_bulk_flip_reports_unknown_uids_as_failed():
    initial = {"stocks": [make_stock("stock-1")], "crosses": []}

    _, batched_db, results = _run_both(initial, ["stock-1", "ghost-9"])

    assert [entry["uid"] for entry in results["success"]] == ["stock-1"]
    assert results["failed"] == [
        {"uid": "ghost-9", "reason": "UID not recognized for this user."}
    ]


def test_bulk_flip_writes_one_activity_document_per_success():
    initial = {"stocks": [make_stock("stock-1"), make_stock("stock-2")], "crosses": []}

    _, batched_db, _ = _run_both(initial, ["stock-1", "stock-2"])

    assert len(batched_db.data.get("activity", [])) == 2


def test_bulk_change_status_matches_sequential():
    initial = {
        "stocks": [make_stock("stock-1"), make_stock("stock-2")],
        "crosses": [make_cross("cross-1")],
    }
    uids = ["stock-1", "cross-1", "stock-2"]
    status = "Needs refresh"
    comment = "batch note"

    frozen = "2026-02-01 09:00:00"
    with patch("flymanager.utils.mongo_records.current_timestamp", return_value=frozen):
        sequential_db = FakeDatabase(copy.deepcopy(initial))
        for uid in uids:
            stock = sequential_db["stocks"].find_one({"UniqueID": uid, "User": USER})
            if stock:
                updates = {"Status": status}
                existing = str(stock.get("Comments", "") or "").strip()
                updates["Comments"] = f"{comment}; {existing}" if existing else comment
                edit_stock(stock["User"], uid, sequential_db, updates, refresh_vials=False)
                continue
            cross = sequential_db["crosses"].find_one({"UniqueID": uid, "User": USER})
            if cross:
                updates = {"Status": status}
                existing = str(cross.get("Comments", "") or "").strip()
                updates["Comments"] = f"{comment}; {existing}" if existing else comment
                edit_cross(cross["User"], uid, sequential_db, updates, refresh_vials=False)

        batched_db = FakeDatabase(copy.deepcopy(initial))
        results = bulk_change_status_records(
            USER, uids, batched_db, status, comment=comment
        )

    assert sequential_db.data["stocks"] == batched_db.data["stocks"]
    assert sequential_db.data["crosses"] == batched_db.data["crosses"]
    assert results["failed"] == []
