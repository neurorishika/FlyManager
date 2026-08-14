import flymanager.app  # noqa: F401  (see tests/test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.materialized_cache import backfill_materialized_cache


def test_backfill_materialized_cache_counts_and_skips_a_raising_record():
    db = FakeDatabase({
        "stocks": [
            {"_id": 1, "UniqueID": "s1", "User": "alice", "Cache": None},
            {"_id": 2, "UniqueID": "s2", "User": "alice", "Cache": None},
        ]
    })
    collection = db["stocks"]

    def builder(record):
        if record["UniqueID"] == "s1":
            raise RuntimeError("boom")
        return {"value": "ok"}

    summary = backfill_materialized_cache(
        collection,
        cache_field="Cache",
        cache_getter=lambda record: record.get("Cache"),
        cache_builder=builder,
        query={},
    )

    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 0, "errors": 1}
    assert collection.find_one({"UniqueID": "s1"})["Cache"] is None
    assert collection.find_one({"UniqueID": "s2"})["Cache"] == {"value": "ok"}


def test_backfill_materialized_cache_dry_run_still_never_calls_builder():
    db = FakeDatabase({"stocks": [{"_id": 1, "UniqueID": "s1", "User": "alice", "Cache": None}]})
    collection = db["stocks"]
    calls = []

    def builder(record):
        calls.append(record["UniqueID"])
        return {"value": "ok"}

    summary = backfill_materialized_cache(
        collection,
        cache_field="Cache",
        cache_getter=lambda record: record.get("Cache"),
        cache_builder=builder,
        query={},
        dry_run=True,
    )

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
    assert calls == []
    assert collection.find_one({"UniqueID": "s1"})["Cache"] is None
