import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase


def test_find_one_and_update_returns_after_document():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice", "Status": "Healthy"}]})
    result = db["stocks"].find_one_and_update(
        {"UniqueID": "s1", "User": "alice"},
        {"$set": {"Status": "Sick"}},
        return_document="AFTER",
    )
    assert result["Status"] == "Sick"
    assert db["stocks"].find_one({"UniqueID": "s1"})["Status"] == "Sick"


def test_delete_many_removes_matching_and_returns_count():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "No longer maintained"},
            {"UniqueID": "s2", "User": "alice", "Status": "Healthy"},
        ]
    })
    result = db["stocks"].delete_many({"UniqueID": {"$in": ["s1", "s2"]}, "Status": "No longer maintained"})
    assert result.deleted_count == 1
    assert [s["UniqueID"] for s in db["stocks"].find({})] == ["s2"]


def test_find_supports_sort_skip_limit_chaining():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Name": "b"},
            {"UniqueID": "s2", "User": "alice", "Name": "a"},
            {"UniqueID": "s3", "User": "alice", "Name": "c"},
        ]
    })
    cursor = db["stocks"].find({"User": "alice"}).sort("Name", 1).skip(1).limit(1)
    assert [doc["UniqueID"] for doc in cursor] == ["s1"]


def test_distinct_returns_unique_non_null_values():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "TrayID": "T1"},
            {"UniqueID": "s2", "User": "alice", "TrayID": "T2"},
            {"UniqueID": "s3", "User": "alice", "TrayID": "T1"},
            {"UniqueID": "s4", "User": "alice", "TrayID": ""},
        ]
    })
    assert sorted(db["stocks"].distinct("TrayID", {"User": "alice"})) == ["T1", "T2"]


def test_aggregate_match_addfields_convert_sort_skip_limit():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "TrayPosition": "10"},
            {"UniqueID": "s2", "User": "alice", "TrayPosition": "2"},
            {"UniqueID": "s3", "User": "alice", "TrayPosition": "not-a-number"},
        ]
    })
    pipeline = [
        {"$match": {"User": "alice"}},
        {"$addFields": {"_sortPos": {"$convert": {"input": "$TrayPosition", "to": "double", "onError": 0, "onNull": 0}}}},
        {"$sort": {"_sortPos": 1}},
        {"$skip": 0},
        {"$limit": 2},
    ]
    result = list(db["stocks"].aggregate(pipeline))
    assert [doc["UniqueID"] for doc in result] == ["s3", "s2"]


def test_find_supports_or_query():
    db = FakeDatabase({
        "trays": [
            {"UniqueID": "t1", "User": "bob", "TrayID": "T1"},
            {"UniqueID": "t2", "User": "carol", "TrayID": "T2"},
            {"UniqueID": "t3", "User": "dave", "TrayID": "T3"},
        ]
    })
    result = list(db["trays"].find({"$or": [
        {"User": "bob", "TrayID": "T1"},
        {"User": "dave", "TrayID": "T3"},
    ]}))
    assert sorted(t["UniqueID"] for t in result) == ["t1", "t3"]
