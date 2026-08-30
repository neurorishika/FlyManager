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


def test_distinct_returns_unique_values_including_blanks_like_real_mongo():
    # The real MongoDB driver's distinct() returns "" and None verbatim - it
    # does not filter them out. Callers are responsible for that filtering
    # (see _compute_stock_unique_values/_compute_cross_unique_values), so the
    # fake must match that behavior rather than silently hiding blanks that
    # production code failed to filter.
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "TrayID": "T1"},
            {"UniqueID": "s2", "User": "alice", "TrayID": "T2"},
            {"UniqueID": "s3", "User": "alice", "TrayID": "T1"},
            {"UniqueID": "s4", "User": "alice", "TrayID": ""},
        ]
    })
    assert sorted(db["stocks"].distinct("TrayID", {"User": "alice"})) == ["", "T1", "T2"]


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


def test_find_supports_and_query():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "Healthy"},
            {"UniqueID": "s2", "User": "alice", "Status": "Sick"},
            {"UniqueID": "s3", "User": "bob", "Status": "Healthy"},
        ]
    })
    result = list(db["stocks"].find({"$and": [{"User": "alice"}, {"Status": "Healthy"}]}))
    assert [doc["UniqueID"] for doc in result] == ["s1"]


def test_find_supports_ne_nin_and_regex_operators():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "Status": "Healthy", "AssignedTo": "", "Provenance": "Bloomington/12345"},
            {"UniqueID": "s2", "Status": "No longer maintained", "AssignedTo": "bob", "Provenance": "Other/x"},
            {"UniqueID": "s3", "Status": "Sick", "AssignedTo": "alice", "Provenance": "Bloomington"},
        ]
    })
    assert sorted(doc["UniqueID"] for doc in db["stocks"].find({"Status": {"$ne": "No longer maintained"}})) == ["s1", "s3"]
    assert sorted(doc["UniqueID"] for doc in db["stocks"].find({"AssignedTo": {"$nin": ["", "alice"]}})) == ["s2"]
    assert sorted(doc["UniqueID"] for doc in db["stocks"].find({"Provenance": {"$regex": "^Bloomington(/|$)"}})) == ["s1", "s3"]


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


def test_find_supports_dotted_paths_and_array_containment():
    """_apply_set understood dotted paths but _matches did not.

    A query like {"match.markerKeys": "Sb"} silently matched nothing rather
    than raising -- the same silent-failure class as a dropped update
    operator. marker_images is indexed on exactly that path.
    """
    db = FakeDatabase({"marker_images": [
        {"imageId": "a", "match": {"markerKeys": ["Sb", "CyO"], "bodyPart": "wing"}},
        {"imageId": "b", "match": {"markerKeys": ["Dr"], "bodyPart": "eye"}},
    ]})
    assert [d["imageId"] for d in db["marker_images"].find({"match.markerKeys": "Sb"})] == ["a"]
    assert [d["imageId"] for d in db["marker_images"].find({"match.bodyPart": "eye"})] == ["b"]
    assert list(db["marker_images"].find({"match.markerKeys": "nope"})) == []
    assert list(db["marker_images"].find({"match.missing": "x"})) == []


def test_update_one_applies_add_to_set_and_pull():
    db = FakeDatabase({"marker_images": [
        {"imageId": "a", "match": {"markerKeys": ["Sb"]}},
    ]})
    db["marker_images"].update_one({"imageId": "a"}, {"$addToSet": {"match.markerKeys": "CyO"}})
    db["marker_images"].update_one({"imageId": "a"}, {"$addToSet": {"match.markerKeys": "Sb"}})
    assert db["marker_images"].find_one({"imageId": "a"})["match"]["markerKeys"] == ["Sb", "CyO"]

    db["marker_images"].update_one({"imageId": "a"}, {"$pull": {"match.markerKeys": "Sb"}})
    assert db["marker_images"].find_one({"imageId": "a"})["match"]["markerKeys"] == ["CyO"]


def test_update_one_refuses_an_operator_it_cannot_model():
    """Silently dropping a write makes a broken production path look green."""
    import pytest

    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "Note": "x"}]})
    with pytest.raises(NotImplementedError):
        db["stocks"].update_one({"UniqueID": "s1"}, {"$unset": {"Note": ""}})
