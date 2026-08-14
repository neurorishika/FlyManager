import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.access import get_direct_reports, can_assign_to_user


def _users_db():
    return FakeDatabase({
        "users": [
            {"Username": "alice", "ReportsTo": ""},
            {"Username": "bob", "ReportsTo": "alice"},
            {"Username": "carol", "ReportsTo": "alice"},
            {"Username": "dave", "ReportsTo": "bob"},
        ]
    })


def test_get_direct_reports_returns_only_direct_reports():
    db = _users_db()
    assert sorted(get_direct_reports("alice", db)) == ["bob", "carol"]
    assert get_direct_reports("bob", db) == ["dave"]
    assert get_direct_reports("carol", db) == []


def test_get_direct_reports_does_not_scan_full_collection():
    db = _users_db()
    collection = db["users"]
    original_find = collection.__class__.find
    seen_queries = []

    def spy(self, query=None, projection=None):
        seen_queries.append(query or {})
        return original_find(self, query, projection)

    collection.__class__.find = spy
    try:
        get_direct_reports("alice", db)
    finally:
        collection.__class__.find = original_find

    assert seen_queries, "get_direct_reports must query the users collection"
    assert all(query.get("ReportsTo") == "alice" for query in seen_queries), (
        "must filter by ReportsTo server-side, not fetch {} and filter in Python"
    ).format(seen_queries)


def test_can_assign_to_user_allows_direct_report():
    db = _users_db()
    assert can_assign_to_user("alice", "bob", db) is True


def test_can_assign_to_user_denies_non_report():
    db = _users_db()
    assert can_assign_to_user("alice", "dave", db) is False
