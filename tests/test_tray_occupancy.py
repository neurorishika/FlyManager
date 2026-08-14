import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.trays import get_tray_occupancy


def _stock(uid, position, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "TrayID": "T1", "TrayPosition": position,
        "Name": uid, "Genotype": "w[1118]", "Status": "Healthy",
        "VialLifetime": "28", "FlipFrequency": "7",
    }
    stock.update(overrides)
    return stock


def _cross(uid, position, male_id, female_id, **overrides):
    cross = {
        "UniqueID": uid, "User": "alice", "TrayID": "T1", "TrayPosition": position,
        "Name": uid, "MaleGenotype": "w[1118]", "FemaleGenotype": "w[1118]",
        "Status": "Healthy", "VialLifetime": "14", "FlipFrequency": "7",
        "MaleUniqueID": male_id, "FemaleUniqueID": female_id,
    }
    cross.update(overrides)
    return cross


def test_get_tray_occupancy_resolves_cross_parent_ids():
    db = FakeDatabase({
        "stocks": [_stock("m1", "5"), _stock("f1", "6")],
        "crosses": [_cross("x1", "1", "m1", "f1")],
    })
    occupancy = get_tray_occupancy("alice", "T1", db)
    assert occupancy["1"]["male_stock_id"] == "m1"
    assert occupancy["1"]["female_stock_id"] == "f1"


def test_get_tray_occupancy_batches_parent_stock_lookup():
    db = FakeDatabase({
        "stocks": [_stock("m1", "5"), _stock("f1", "6"), _stock("m2", "7"), _stock("f2", "8")],
        "crosses": [
            _cross("x1", "1", "m1", "f1"),
            _cross("x2", "2", "m2", "f2"),
        ],
    })
    collection = db["stocks"]
    original_find_one = collection.__class__.find_one
    find_one_calls = []

    def spy(self, *args, **kwargs):
        find_one_calls.append((args, kwargs))
        return original_find_one(self, *args, **kwargs)

    collection.__class__.find_one = spy
    try:
        get_tray_occupancy("alice", "T1", db)
    finally:
        collection.__class__.find_one = original_find_one

    assert len(find_one_calls) == 0, (
        "parent stock names must be resolved with one batched $in query, not "
        "one find_one() per male/female per cross"
    )


def test_get_tray_occupancy_blocks_positions_for_multi_vial_stock():
    db = FakeDatabase({
        "stocks": [_stock("s1", "1", VialLifetime="28", FlipFrequency="7")],  # requires 4 vials -> ceil(28/7)
        "crosses": [],
    })
    occupancy = get_tray_occupancy("alice", "T1", db)
    assert occupancy["1"]["type"] == "stock"
    assert occupancy["11"]["type"] == "blocked" and occupancy["11"]["blocked_by"] == "1"
    assert occupancy["21"]["type"] == "blocked"
    assert occupancy["31"]["type"] == "blocked"
    assert "41" not in occupancy
