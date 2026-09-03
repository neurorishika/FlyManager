# tests/test_cross_propagation.py
from unittest.mock import patch

import flymanager.app  # noqa: F401  (see tests/test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.crosses import propagate_stock_genotype_to_crosses


def _cross(uid, male_uid, female_uid, male_genotype="w[*]", female_genotype="w[*]", user="alice"):
    return {
        "UniqueID": uid, "User": user, "AssignedTo": "",
        "MaleUniqueID": male_uid, "FemaleUniqueID": female_uid,
        "MaleGenotype": male_genotype, "FemaleGenotype": female_genotype,
        "PhenotypeCache": {"old": True}, "StandardizationCache": {"old": True},
    }


def test_propagate_updates_cross_where_stock_is_male_parent():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID1", female_uid="SID2")],
    })

    with patch("flymanager.utils.mongo.crosses.schedule_record_cache_generation") as schedule:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]; CyO/+", db)

    assert summary == {"crosses_updated": 1, "errors": 0}
    updated = db["crosses"].find_one({"UniqueID": "CID1"})
    assert updated["MaleGenotype"] == "w[*]; CyO/+"
    assert updated["FemaleGenotype"] == "w[*]"
    assert updated["PhenotypeCache"] is None
    assert updated["StandardizationCache"] is None
    assert updated["CacheGeneration"]["state"] == "pending"
    schedule.assert_called_once()


def test_propagate_updates_cross_where_stock_is_female_parent():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID2", female_uid="SID1")],
    })

    with patch(
        "flymanager.utils.mongo.crosses.schedule_record_cache_generation",
    ):
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]; CyO/+", db)

    assert summary == {"crosses_updated": 1, "errors": 0}
    updated = db["crosses"].find_one({"UniqueID": "CID1"})
    assert updated["FemaleGenotype"] == "w[*]; CyO/+"
    assert updated["MaleGenotype"] == "w[*]"


def test_propagate_handles_self_cross_updating_both_sides():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID1", female_uid="SID1")],
    })

    with patch(
        "flymanager.utils.mongo.crosses.schedule_record_cache_generation",
    ):
        propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]; CyO/+", db)

    updated = db["crosses"].find_one({"UniqueID": "CID1"})
    assert updated["MaleGenotype"] == "w[*]; CyO/+"
    assert updated["FemaleGenotype"] == "w[*]; CyO/+"


def test_propagate_is_noop_for_stock_with_no_dependent_crosses():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID9", female_uid="SID8")],
    })

    with patch("flymanager.utils.mongo.crosses.schedule_record_cache_generation") as schedule:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]", db)

    assert summary == {"crosses_updated": 0, "errors": 0}
    schedule.assert_not_called()


def test_propagate_only_touches_crosses_owned_by_same_user():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID1", female_uid="SID2", user="bob")],
    })

    with patch("flymanager.utils.mongo.crosses.schedule_record_cache_generation") as schedule:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]", db)

    assert summary == {"crosses_updated": 0, "errors": 0}
    schedule.assert_not_called()


def test_propagate_marks_every_dependent_cross_pending_without_building_caches():
    db = FakeDatabase({
        "crosses": [
            _cross("bad", male_uid="SID1", female_uid="SID2", female_genotype="raise-trigger"),
            _cross("good", male_uid="SID1", female_uid="SID3", female_genotype="w[*]"),
        ],
    })

    with patch("flymanager.utils.mongo.crosses.schedule_record_cache_generation") as schedule:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "edited-genotype", db)

    assert summary == {"crosses_updated": 2, "errors": 0}
    assert db["crosses"].find_one({"UniqueID": "good"})["MaleGenotype"] == "edited-genotype"
    assert db["crosses"].find_one({"UniqueID": "bad"})["MaleGenotype"] == "edited-genotype"
    assert schedule.call_count == 2
