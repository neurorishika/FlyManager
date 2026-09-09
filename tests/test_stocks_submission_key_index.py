"""The stocks (User, SubmissionKey) unique index must survive real data.

It was introduced sparse, which on a COMPOUND index only skips a document
missing EVERY indexed field. Every stock has a User, so every stock without a
SubmissionKey was indexed as (User, null) and the second one collided. Index
creation runs at import, so that failure was not one broken query -- it was
the app refusing to start at all, on any database with more than one stock
predating submission keys.
"""
import os

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from flymanager.app import create_app  # noqa: F401  (breaks a circular import)
from flymanager.utils.mongo.db import ensure_mongo_indexes

INDEX = "stocks_user_submission_key"


@pytest.fixture
def database():
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client["flymanager_index_test"]
    client.drop_database("flymanager_index_test")
    yield db
    client.drop_database("flymanager_index_test")
    client.close()


def _stocks_without_submission_keys(db):
    db["stocks"].insert_many([
        {"User": "rmohanta", "UniqueID": "a"},
        {"User": "rmohanta", "UniqueID": "b"},
        {"User": "rmohanta", "UniqueID": "c"},
    ])


def test_many_stocks_with_no_submission_key_do_not_collide(database):
    _stocks_without_submission_keys(database)
    ensure_mongo_indexes(database)
    names = {index["name"] for index in database["stocks"].list_indexes()}
    assert INDEX in names


def test_the_index_still_enforces_uniqueness_where_it_applies(database):
    ensure_mongo_indexes(database)
    database["stocks"].insert_one(
        {"User": "rmohanta", "UniqueID": "a", "SubmissionKey": "k1"})
    with pytest.raises(OperationFailure):
        database["stocks"].insert_one(
            {"User": "rmohanta", "UniqueID": "b", "SubmissionKey": "k1"})


def test_the_same_key_under_a_different_user_is_allowed(database):
    ensure_mongo_indexes(database)
    database["stocks"].insert_many([
        {"User": "rmohanta", "UniqueID": "a", "SubmissionKey": "k1"},
        {"User": "someone-else", "UniqueID": "b", "SubmissionKey": "k1"},
    ])
    assert database["stocks"].count_documents({"SubmissionKey": "k1"}) == 2


def test_a_null_submission_key_is_still_indexed(database):
    """An explicit null is a value, not an absence, so it stays constrained --
    partialFilterExpression only excludes the field being absent."""
    ensure_mongo_indexes(database)
    database["stocks"].insert_one(
        {"User": "rmohanta", "UniqueID": "a", "SubmissionKey": None})
    with pytest.raises(OperationFailure):
        database["stocks"].insert_one(
            {"User": "rmohanta", "UniqueID": "b", "SubmissionKey": None})


def test_an_existing_sparse_index_is_replaced_rather_than_conflicting(database):
    """Mongo refuses to redefine an index in place, and index creation runs at
    import -- so a deployment that already built the sparse version would have
    failed every startup after the definition changed."""
    # No colliding data here on purpose: the sparse index could only ever have
    # been built where the data happened to allow it, which is exactly the
    # deployment that then upgrades into the conflict.
    database["stocks"].create_index(
        [("User", 1), ("SubmissionKey", 1)], unique=True, sparse=True, name=INDEX)
    ensure_mongo_indexes(database)
    index = next(i for i in database["stocks"].list_indexes() if i["name"] == INDEX)
    assert index.get("partialFilterExpression") == {"SubmissionKey": {"$exists": True}}
    assert not index.get("sparse")
