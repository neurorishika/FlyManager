import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.access import get_accessible_documents_page
from flymanager.app.routes.stock import (
    _apply_stock_filters, _apply_stock_search, _build_stock_mongo_filter,
    _stock_sort_key,
)
from flymanager.app.routes.cross import (
    _apply_cross_filters, _apply_cross_search, _build_cross_mongo_filter,
    _cross_sort_key,
)


def _stock(uid, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "AssignedTo": "", "Status": "Healthy",
        "Type": "WT", "TrayID": "", "TrayPosition": "", "Name": uid,
    }
    stock.update(overrides)
    return stock


def _cross(uid, **overrides):
    cross = {
        "UniqueID": uid, "User": "alice", "AssignedTo": "", "Status": "Healthy",
        "MaleSpecies": "D. melanogaster", "FemaleSpecies": "D. melanogaster",
        "TrayID": "", "TrayPosition": "", "Name": uid,
    }
    cross.update(overrides)
    return cross


# --- Step 3: get_accessible_documents_page -----------------------------

def test_filters_scoped_to_owner_and_deterministic_filter():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", Status="Healthy"),
            _stock("s2", Status="Sick"),
            _stock("s3", User="bob", Status="Healthy"),
            _stock("s4", User="bob", AssignedTo="alice", Status="Healthy"),
        ]
    })
    items, total = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter={"Status": "Healthy"},
    )
    assert total == 2
    assert sorted(item["UniqueID"] for item in items) == ["s1", "s4"]


def test_sorts_by_trayid_then_numeric_trayposition():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", TrayID="T1", TrayPosition="10"),
            _stock("s2", TrayID="T1", TrayPosition="2"),
            _stock("s3", TrayID="T1", TrayPosition="1"),
        ]
    })
    items, _ = get_accessible_documents_page("stocks", "alice", db)
    assert [item["UniqueID"] for item in items] == ["s3", "s2", "s1"]


def test_skip_and_limit_paginate_at_the_database_level():
    db = FakeDatabase({
        "stocks": [_stock(f"s{i}", TrayID="T1", TrayPosition=str(i)) for i in range(5)]
    })
    items, total = get_accessible_documents_page("stocks", "alice", db, skip=2, limit=2)
    assert total == 5
    assert [item["UniqueID"] for item in items] == ["s2", "s3"]


def test_projection_limits_returned_fields():
    db = FakeDatabase({"stocks": [_stock("s1", Genotype="w[1118]", Comments="note")]})
    items, _ = get_accessible_documents_page(
        "stocks", "alice", db, projection={"UniqueID", "Name"},
    )
    assert set(items[0].keys()) == {"UniqueID", "Name"}


# --- Step 6: stock deterministic-filter translator ----------------------

def test_build_stock_mongo_filter_excludes_no_longer_maintained_by_default():
    assert _build_stock_mongo_filter({}) == {"Status": {"$ne": "No longer maintained"}}


def test_build_stock_mongo_filter_combines_multiple_fields():
    # No filterStatus supplied -> the "exclude No longer maintained by
    # default" clause is also appended (see the "excludes by default" test
    # above), so three clauses combine here, not two.
    result = _build_stock_mongo_filter({"filterType": "WT", "filterTrayID": "T1"})
    assert result == {
        "$and": [
            {"Type": "WT"},
            {"TrayID": "T1"},
            {"Status": {"$ne": "No longer maintained"}},
        ]
    }


def test_build_stock_mongo_filter_provenance_matches_prefix_before_slash():
    # No filterStatus supplied -> the "exclude No longer maintained by
    # default" clause is also appended, same as the "combines" test above.
    result = _build_stock_mongo_filter({"filterProvenance": "Bloomington"})
    assert result == {
        "$and": [
            {"Status": {"$ne": "No longer maintained"}},
            {"Provenance": {"$regex": "^Bloomington(/|$)"}},
        ]
    }


# --- Step 7: cross deterministic-filter translator ----------------------

def test_build_cross_mongo_filter_excludes_no_longer_maintained_by_default():
    assert _build_cross_mongo_filter({}) == {"Status": {"$ne": "No longer maintained"}}


def test_build_cross_mongo_filter_combines_multiple_fields():
    # No filterStatus supplied -> the "exclude No longer maintained by
    # default" clause is also appended, same as the stock-side equivalent.
    result = _build_cross_mongo_filter({"filterMaleSpecies": "D. melanogaster", "filterTrayID": "T1"})
    assert result == {
        "$and": [
            {"MaleSpecies": "D. melanogaster"},
            {"TrayID": "T1"},
            {"Status": {"$ne": "No longer maintained"}},
        ]
    }


# --- Step 11: equivalence tests -----------------------------------------

def test_pushdown_path_matches_python_path_for_deterministic_filters():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", TrayID="T1", TrayPosition="3", Type="WT", Status="Healthy"),
            _stock("s2", TrayID="T1", TrayPosition="1", Type="WT", Status="Healthy"),
            _stock("s3", TrayID="T2", TrayPosition="1", Type="Mutant", Status="Healthy"),
            _stock("s4", TrayID="T1", TrayPosition="2", Type="WT", Status="No longer maintained"),
        ]
    })
    filter_state = {"filterType": "WT"}

    old_result = sorted(
        _apply_stock_filters(list(db["stocks"].find({"User": "alice"})), filter_state),
        key=_stock_sort_key,
    )
    new_result, _ = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter=_build_stock_mongo_filter(filter_state),
    )

    assert [s["UniqueID"] for s in old_result] == [s["UniqueID"] for s in new_result]


def test_pushdown_plus_python_search_matches_fully_python_path():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", Name="rescue line alpha", Genotype="w[1118]; UAS-x"),
            _stock("s2", Name="control", Genotype="w[1118]"),
        ]
    })
    filter_state = {"searchQuery": "rescue"}

    old_result = sorted(
        _apply_stock_filters(list(db["stocks"].find({"User": "alice"})), filter_state),
        key=_stock_sort_key,
    )
    candidates, _ = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter=_build_stock_mongo_filter(filter_state), limit=None,
    )
    new_result = sorted(
        _apply_stock_search(candidates, filter_state["searchQuery"]), key=_stock_sort_key,
    )

    assert [s["UniqueID"] for s in old_result] == [s["UniqueID"] for s in new_result]


def test_pushdown_path_matches_python_path_for_cross_deterministic_filters():
    db = FakeDatabase({
        "crosses": [
            _cross("c1", TrayID="T1", TrayPosition="3", MaleSpecies="D. melanogaster", Status="Healthy"),
            _cross("c2", TrayID="T1", TrayPosition="1", MaleSpecies="D. melanogaster", Status="Healthy"),
            _cross("c3", TrayID="T2", TrayPosition="1", MaleSpecies="D. simulans", Status="Healthy"),
            _cross("c4", TrayID="T1", TrayPosition="2", MaleSpecies="D. melanogaster", Status="No longer maintained"),
        ]
    })
    filter_state = {"filterMaleSpecies": "D. melanogaster"}

    old_result = sorted(
        _apply_cross_filters(list(db["crosses"].find({"User": "alice"})), filter_state),
        key=_cross_sort_key,
    )
    new_result, _ = get_accessible_documents_page(
        "crosses", "alice", db, mongo_filter=_build_cross_mongo_filter(filter_state),
    )

    assert [c["UniqueID"] for c in old_result] == [c["UniqueID"] for c in new_result]


def test_pushdown_plus_python_search_matches_fully_python_path_for_cross():
    db = FakeDatabase({
        "crosses": [
            _cross("c1", Name="rescue line alpha", TrayID="T1", TrayPosition="1", Comments=""),
            _cross("c2", Name="control", TrayID="T1", TrayPosition="2", Comments=""),
        ]
    })
    filter_state = {"searchQuery": "rescue"}

    old_result = sorted(
        _apply_cross_filters(list(db["crosses"].find({"User": "alice"})), filter_state),
        key=_cross_sort_key,
    )
    candidates, _ = get_accessible_documents_page(
        "crosses", "alice", db, mongo_filter=_build_cross_mongo_filter(filter_state), limit=None,
    )
    new_result = sorted(
        _apply_cross_search(candidates, filter_state["searchQuery"]), key=_cross_sort_key,
    )

    assert [c["UniqueID"] for c in old_result] == [c["UniqueID"] for c in new_result]
