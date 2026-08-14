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
from flymanager.app.routes.explorer_utils import compute_explorer_scope_counts


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
        extra_sort_keys=(),
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


# --- Fix round 1: out-of-range page clamps instead of returning empty --

def test_skip_past_last_page_clamps_to_last_page_instead_of_empty():
    db = FakeDatabase({
        "stocks": [_stock(f"s{i}", TrayID="T1", TrayPosition=str(i)) for i in range(5)]
    })
    # 5 items, per_page=2 -> 3 pages (offsets 0, 2, 4). Request an
    # out-of-range page (skip=20, far past the last valid offset of 4).
    items, total = get_accessible_documents_page("stocks", "alice", db, skip=20, limit=2)
    assert total == 5
    # Must show the last valid page's actual content (s4), not an empty list.
    assert [item["UniqueID"] for item in items] == ["s4"]


def test_skip_within_range_is_not_clamped():
    db = FakeDatabase({
        "stocks": [_stock(f"s{i}", TrayID="T1", TrayPosition=str(i)) for i in range(5)]
    })
    items, total = get_accessible_documents_page("stocks", "alice", db, skip=2, limit=2)
    assert total == 5
    assert [item["UniqueID"] for item in items] == ["s2", "s3"]


def test_zero_results_with_skip_stays_empty_and_does_not_error():
    db = FakeDatabase({"stocks": [_stock("s1", Status="Sick")]})
    items, total = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter={"Status": "Healthy"}, skip=10, limit=2,
    )
    assert total == 0
    assert items == []


def test_route_level_out_of_range_page_shows_last_page_items(monkeypatch):
    # Reproduces the reported bug at the route level: _build_pagination_from_db_page
    # clamps the *displayed* page number using total_count, but the skip
    # passed into get_accessible_documents_page must also be clamped or the
    # items returned won't match the displayed page.
    import flymanager.app as flymanager_app_module  # noqa: F401
    from unittest.mock import patch
    from flymanager.app import create_app

    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    with patch("flymanager.app.get_settings", return_value={
        "lab_info": {"lab_name": "Test Lab", "admin_name": "Admin", "admin_email": "a@b.com"},
        "theme": {"accent_color": "#0055aa", "dark_mode": False},
    }):
        app = create_app()
    app.config.update(TESTING=True)

    # per_page must be one of the explorer's allowed values (20/50/100/all)
    # - anything else is normalized back to the default - so use 41 stocks
    # with per_page=20 to get 3 real pages (20, 20, 1).
    db = FakeDatabase({
        "stocks": [
            _stock(
                f"s{i:03d}", TrayID="T1", TrayPosition=str(i),
                NextFlipDates="2026-01-01", NextEclosionDates="2026-01-01",
            )
            for i in range(1, 42)
        ]
    })

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "alice"

        with patch("flymanager.app.routes.stock.db", db):
            # 41 stocks, per_page=20 -> 3 valid pages; request page 99.
            response = client.get("/stock/explorer?page=99&per_page=20")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # The last valid page (page 3) holds only s041 - it must actually
    # render, not an empty result set.
    assert "s041" in body
    assert "Showing 41-41 of 41 stocks" in body


# --- Fix round 1: cross sort tie-break must match _cross_sort_key -------

def test_cross_pushdown_sort_ties_match_cross_sort_key_when_extra_sort_keys_empty():
    # Two crosses sharing the same TrayID+TrayPosition (both blank, the
    # common "un-trayed" case) but with Name values that would reorder them
    # under the stock-shaped 4-key sort. _cross_sort_key only tie-breaks on
    # (TrayID, TrayPosition), so a stable sort preserves original order for
    # ties - the DB-level browse path must match that when extra_sort_keys=().
    db = FakeDatabase({
        "crosses": [
            _cross("c-zebra", Name="Zebra cross", TrayID="", TrayPosition=""),
            _cross("c-alpha", Name="Alpha cross", TrayID="", TrayPosition=""),
        ]
    })
    python_order = [
        c["UniqueID"]
        for c in sorted(list(db["crosses"].find({"User": "alice"})), key=_cross_sort_key)
    ]
    db_order, _ = get_accessible_documents_page(
        "crosses", "alice", db, extra_sort_keys=(),
    )
    assert [c["UniqueID"] for c in db_order] == python_order


def test_cross_pushdown_default_sort_keys_would_diverge_from_cross_sort_key():
    # Sanity check that the two sort specs actually differ for tied
    # TrayID/TrayPosition data (otherwise the fix above wouldn't be testing
    # anything meaningful): the stock-shaped default (Name, UniqueID
    # tie-break) reorders "Zebra" after "Alpha", while _cross_sort_key
    # (no Name/UniqueID tie-break) preserves insertion order.
    db = FakeDatabase({
        "crosses": [
            _cross("c-zebra", Name="Zebra cross", TrayID="", TrayPosition=""),
            _cross("c-alpha", Name="Alpha cross", TrayID="", TrayPosition=""),
        ]
    })
    default_sort_order, _ = get_accessible_documents_page("crosses", "alice", db)
    matching_sort_order, _ = get_accessible_documents_page(
        "crosses", "alice", db, extra_sort_keys=(),
    )
    assert [c["UniqueID"] for c in default_sort_order] != [c["UniqueID"] for c in matching_sort_order]


# --- Fix round 2: never-assigned documents must not count as assigned_out --

def test_scope_counts_never_assigned_stock_is_not_counted_as_assigned_out():
    # A freshly created stock never has an AssignedTo field set at all - it's
    # only ever added via $set by the assignment-update helpers - so a query
    # that treats a missing AssignedTo the same as it treats a real assignee
    # value ({"$nin": ["", username]} without an $exists guard) would wrongly
    # count every never-assigned stock as "assigned out".
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "Healthy"},  # no AssignedTo key
            {"UniqueID": "s2", "User": "alice", "AssignedTo": "", "Status": "Healthy"},
            {"UniqueID": "s3", "User": "alice", "AssignedTo": "bob", "Status": "Healthy"},
        ]
    })
    counts = compute_explorer_scope_counts("stocks", "alice", db)
    assert counts["maintain"] == 2  # s1 (never assigned) + s2 (explicitly returned)
    assert counts["assigned_out"] == 1  # only s3, genuinely assigned to bob
    assert counts["incoming"] == 0


def test_scope_counts_never_assigned_cross_is_not_counted_as_assigned_out():
    db = FakeDatabase({
        "crosses": [
            {"UniqueID": "c1", "User": "alice", "Status": "Healthy"},  # no AssignedTo key
            {"UniqueID": "c2", "User": "alice", "AssignedTo": "bob", "Status": "Healthy"},
        ]
    })
    counts = compute_explorer_scope_counts("crosses", "alice", db)
    assert counts["maintain"] == 1
    assert counts["assigned_out"] == 1
    assert counts["incoming"] == 0


def test_scope_counts_incoming_ignores_documents_missing_assigned_to():
    # A document owned by someone else with no AssignedTo field must never
    # count as "incoming" for a viewer who isn't the owner.
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "bob", "Status": "Healthy"},  # not alice's, no AssignedTo
            {"UniqueID": "s2", "User": "bob", "AssignedTo": "alice", "Status": "Healthy"},
        ]
    })
    counts = compute_explorer_scope_counts("stocks", "alice", db)
    # s1 (bob's, no AssignedTo) is out of alice's scope entirely - it isn't
    # owned by or assigned to her, so it contributes to none of the counts.
    # s2 is genuinely incoming (assigned to alice by bob); total-in-scope is
    # just s2, and it's not assigned_out since alice isn't its User.
    assert counts["incoming"] == 1
    assert counts["assigned_out"] == 0
    assert counts["maintain"] == 1
