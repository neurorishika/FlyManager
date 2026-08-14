import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.phenotypes.backfill import backfill_stock_phenotype_cache


def _stocks_db(count):
    return FakeDatabase({
        "stocks": [
            {"_id": i, "User": "alice", "Genotype": f"w[{i}]", "PhenotypeCache": None}
            for i in range(count)
        ]
    })


def test_backfill_updates_all_records_missing_cache():
    db = _stocks_db(3)
    summary = backfill_stock_phenotype_cache(db["stocks"])
    assert summary["updated"] == 3
    for doc in db["stocks"].find({}):
        assert doc["PhenotypeCache"] is not None


def test_backfill_issues_bulk_write_not_one_update_per_document():
    db = _stocks_db(10)
    collection = db["stocks"]
    counts = {"update_one": 0, "bulk_write": 0}
    original_update_one = collection.__class__.update_one
    original_bulk_write = collection.__class__.bulk_write

    def spy_update_one(self, *args, **kwargs):
        counts["update_one"] += 1
        return original_update_one(self, *args, **kwargs)

    def spy_bulk_write(self, *args, **kwargs):
        counts["bulk_write"] += 1
        return original_bulk_write(self, *args, **kwargs)

    collection.__class__.update_one = spy_update_one
    collection.__class__.bulk_write = spy_bulk_write
    try:
        backfill_stock_phenotype_cache(db["stocks"])
    finally:
        collection.__class__.update_one = original_update_one
        collection.__class__.bulk_write = original_bulk_write

    assert counts["update_one"] == 0
    assert counts["bulk_write"] == 1  # 10 records fits in one 500-doc chunk


def test_backfill_flushes_in_chunks_of_500(monkeypatch):
    db = _stocks_db(1200)
    collection = db["stocks"]
    counts = {"bulk_write": 0}
    original_bulk_write = collection.__class__.bulk_write

    def spy_bulk_write(self, *args, **kwargs):
        counts["bulk_write"] += 1
        return original_bulk_write(self, *args, **kwargs)

    collection.__class__.bulk_write = spy_bulk_write
    try:
        backfill_stock_phenotype_cache(db["stocks"])
    finally:
        collection.__class__.bulk_write = original_bulk_write

    assert counts["bulk_write"] == 3  # ceil(1200 / 500)
