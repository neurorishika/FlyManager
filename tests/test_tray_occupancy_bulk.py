import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.trays import get_tray_occupancy, get_tray_occupancies_bulk


def _stock(uid, owner, tray_id, position, **overrides):
    stock = {
        "UniqueID": uid, "User": owner, "TrayID": tray_id, "TrayPosition": position,
        "Name": uid, "Genotype": "w[1118]", "Status": "Healthy",
        "VialLifetime": "28", "FlipFrequency": "7",
    }
    stock.update(overrides)
    return stock


def test_get_tray_occupancies_bulk_matches_per_tray_results():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", "alice", "T1", "1"),
            _stock("s2", "alice", "T2", "1"),
            _stock("s3", "bob", "T1", "1"),
        ],
        "crosses": [],
    })
    trays = [
        {"UniqueID": "tray-a-1", "User": "alice", "TrayID": "T1"},
        {"UniqueID": "tray-a-2", "User": "alice", "TrayID": "T2"},
        {"UniqueID": "tray-b-1", "User": "bob", "TrayID": "T1"},
    ]

    bulk_result = get_tray_occupancies_bulk(trays, db)

    for tray in trays:
        expected = get_tray_occupancy(tray["User"], tray["TrayID"], db)
        assert bulk_result[tray["UniqueID"]] == expected


def test_get_tray_occupancies_bulk_issues_exactly_three_queries():
    db = FakeDatabase({
        "stocks": [_stock(f"s{i}", "alice", f"T{i}", "1") for i in range(5)],
        "crosses": [],
    })
    trays = [{"UniqueID": f"tray-{i}", "User": "alice", "TrayID": f"T{i}"} for i in range(5)]

    # FakeCollection is a single shared class across all collection names
    # (db["stocks"] and db["crosses"] are distinct instances of the *same*
    # class), so patching `__class__.find` once per collection name would
    # chain the spies together and double-count every call. Patch it exactly
    # once and dispatch on `self.name` instead.
    query_count = {"stocks": 0, "crosses": 0}
    collection_class = db["stocks"].__class__
    original_find = collection_class.find

    def spy(self, *args, **kwargs):
        if self.name in query_count:
            query_count[self.name] += 1
        return original_find(self, *args, **kwargs)

    collection_class.find = spy
    try:
        get_tray_occupancies_bulk(trays, db)
    finally:
        collection_class.find = original_find

    assert query_count["stocks"] == 1  # no crosses -> no parent lookup needed
    assert query_count["crosses"] == 1


def test_get_tray_occupancies_bulk_empty_input_returns_empty_dict():
    db = FakeDatabase({})
    assert get_tray_occupancies_bulk([], db) == {}
