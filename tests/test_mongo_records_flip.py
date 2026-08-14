import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo_records import flip_owned_document


def test_flip_owned_document_returns_none_when_missing():
    db = FakeDatabase({"stocks": []})
    assert flip_owned_document("stocks", "alice", "missing", db, "2026-02-01 09:00") is None


def test_flip_owned_document_returns_updated_document_with_new_flip_log():
    db = FakeDatabase({
        "stocks": [{
            "UniqueID": "s1", "User": "alice",
            "FlipLog": "V1, 2026-01-01 10:00",
            "CurrentlyAliveVials": "V1",
            "Status": "Healthy",
        }]
    })
    result = flip_owned_document("stocks", "alice", "s1", db, "2026-02-01 09:00")

    assert result["FlipLog"] == "V2, 2026-02-01 09:00; V1, 2026-01-01 10:00"
    assert result["CurrentlyAliveVials"] == "V1, V2"
    assert result["LastFlipDate"] == "2026-02-01 09:00"
    stored = db["stocks"].find_one({"UniqueID": "s1"})
    assert stored["FlipLog"] == result["FlipLog"]


def test_flip_owned_document_applies_new_status_and_comment():
    db = FakeDatabase({
        "stocks": [{
            "UniqueID": "s1", "User": "alice",
            "FlipLog": "V1, 2026-01-01 10:00",
            "CurrentlyAliveVials": "V1",
            "Status": "Healthy",
            "Comments": "",
        }]
    })
    result = flip_owned_document(
        "stocks", "alice", "s1", db, "2026-02-01 09:00",
        new_status="Sick", added_comment="looks unwell",
    )
    assert result["Status"] == "Sick"
    assert result["Comments"] == "looks unwell"


def test_flip_owned_document_only_calls_find_once():
    db = FakeDatabase({
        "stocks": [{
            "UniqueID": "s1", "User": "alice",
            "FlipLog": "V1, 2026-01-01 10:00",
            "CurrentlyAliveVials": "V1",
            "Status": "Healthy",
        }]
    })
    collection = db["stocks"]
    call_counts = {"find_one": 0, "update_one": 0, "find_one_and_update": 0}
    original_find_one = collection.__class__.find_one
    original_update_one = collection.__class__.update_one
    original_find_one_and_update = collection.__class__.find_one_and_update

    def counting_find_one(self, *args, **kwargs):
        call_counts["find_one"] += 1
        return original_find_one(self, *args, **kwargs)

    def counting_update_one(self, *args, **kwargs):
        call_counts["update_one"] += 1
        return original_update_one(self, *args, **kwargs)

    def counting_find_one_and_update(self, *args, **kwargs):
        call_counts["find_one_and_update"] += 1
        return original_find_one_and_update(self, *args, **kwargs)

    collection.__class__.find_one = counting_find_one
    collection.__class__.update_one = counting_update_one
    collection.__class__.find_one_and_update = counting_find_one_and_update
    try:
        flip_owned_document("stocks", "alice", "s1", db, "2026-02-01 09:00")
    finally:
        collection.__class__.find_one = original_find_one
        collection.__class__.update_one = original_update_one
        collection.__class__.find_one_and_update = original_find_one_and_update

    assert call_counts["find_one"] == 1  # only the initial existence read
    assert call_counts["update_one"] == 0
    assert call_counts["find_one_and_update"] == 1
