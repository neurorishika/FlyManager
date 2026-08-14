from unittest.mock import patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.stocks import edit_stock


def _stock(uid, user="alice", genotype="w[*]"):
    return {
        "UniqueID": uid, "User": user, "AssignedTo": "",
        "Genotype": genotype, "PhenotypeCache": {}, "StandardizationCache": {},
    }


def test_edit_stock_propagates_genotype_change_to_dependent_crosses():
    db = FakeDatabase({"stocks": [_stock("SID1")]})

    with patch(
        "flymanager.utils.mongo.stocks.build_stock_phenotype_cache",
        return_value={"phenotype": "new"},
    ), patch(
        "flymanager.utils.mongo.stocks.build_stock_standardization_cache",
        return_value={"standardization": "new"},
    ), patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
        return_value={"crosses_updated": 2, "errors": 0},
    ) as propagate:
        success = edit_stock("alice", "SID1", db, {"Genotype": "w[*]; CyO/+"}, refresh_vials=False)

    assert success is True
    propagate.assert_called_once_with("alice", "SID1", "w[*]; CyO/+", db)


def test_edit_stock_does_not_propagate_when_genotype_unchanged():
    db = FakeDatabase({"stocks": [_stock("SID1")]})

    with patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
    ) as propagate:
        success = edit_stock("alice", "SID1", db, {"AssignedTo": "bob"}, refresh_vials=False)

    assert success is True
    propagate.assert_not_called()


def test_edit_stock_does_not_propagate_when_write_fails():
    db = FakeDatabase({"stocks": [_stock("SID1")]})

    with patch(
        "flymanager.utils.mongo.stocks.build_stock_phenotype_cache",
        return_value={"phenotype": "new"},
    ), patch(
        "flymanager.utils.mongo.stocks.build_stock_standardization_cache",
        return_value={"standardization": "new"},
    ), patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
    ) as propagate:
        success = edit_stock("alice", "SID_MISSING", db, {"Genotype": "w[*]; CyO/+"}, refresh_vials=False)

    assert success is False
    propagate.assert_not_called()
