import os

from dotenv import load_dotenv
from pymongo import MongoClient

# Load environment variables from .env file
load_dotenv()


def get_mongo_uri():
    """Return the configured MongoDB URI with a sensible local default."""
    return os.getenv("MONGO_URI", "mongodb://mongodb:27017").strip()


def get_mongo_db_name():
    """Return the configured MongoDB database name with a sensible default."""
    return os.getenv("MONGO_DB_NAME", "flymanager").strip()

def create_mongo_client():
    """
    Create a MongoDB client instance using the connection string from environment variables.
    
    Returns:
    client: pymongo.MongoClient
        The MongoDB client instance.
    """
    mongo_uri = get_mongo_uri()
    client = MongoClient(
        mongo_uri,
        serverSelectionTimeoutMS=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
        connectTimeoutMS=int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    )
    return client

def get_database(client):
    """
    Get the database instance from the MongoDB client.
    
    Parameters:
    client: pymongo.MongoClient
        The MongoDB client instance.
    
    Returns:
    db: pymongo.database.Database
        The database instance.
    """
    db_name = get_mongo_db_name()
    return client[db_name]


def _create_index_replacing_conflict(collection, keys, **options):
    """Create an index, replacing one of the same name built with other options.

    Mongo refuses to redefine an index in place: same name, different options
    raises IndexOptionsConflict. A deployment that already built the earlier
    definition of an index would therefore fail every startup after the
    definition changed -- and index creation runs at import, so that failure
    is the whole app, not one request.
    """
    from pymongo.errors import OperationFailure

    try:
        return collection.create_index(keys, **options)
    except OperationFailure as exc:
        # 85 IndexOptionsConflict, 86 IndexKeySpecsConflict.
        if exc.code not in (85, 86):
            raise
        collection.drop_index(options["name"])
        return collection.create_index(keys, **options)


def ensure_mongo_indexes(db):
    """Create the indexes used by stock/cross access, tray, and auth query patterns."""
    db["stocks"].create_index([("User", 1), ("UniqueID", 1)], name="stocks_user_uid")
    db["stocks"].create_index([("AssignedTo", 1), ("UniqueID", 1)], name="stocks_assigned_uid")
    # partialFilterExpression, not sparse: on a COMPOUND index sparse only
    # skips a document missing EVERY indexed field, and every stock has a
    # User. So a sparse unique index here indexes each stock with no
    # SubmissionKey as (User, null) and the second one collides -- which took
    # down index creation, and with it the whole app, on any database with
    # more than one stock predating submission keys.
    _create_index_replacing_conflict(
        db["stocks"],
        [("User", 1), ("SubmissionKey", 1)],
        unique=True,
        partialFilterExpression={"SubmissionKey": {"$exists": True}},
        name="stocks_user_submission_key",
    )
    db["stocks"].create_index([("User", 1), ("Status", 1), ("TrayID", 1)], name="stocks_user_status_tray")
    db["stocks"].create_index([("AssignedTo", 1), ("Status", 1), ("TrayID", 1)], name="stocks_assigned_status_tray")

    db["crosses"].create_index([("User", 1), ("UniqueID", 1)], name="crosses_user_uid")
    db["crosses"].create_index([("AssignedTo", 1), ("UniqueID", 1)], name="crosses_assigned_uid")
    db["crosses"].create_index([("User", 1), ("Status", 1), ("TrayID", 1)], name="crosses_user_status_tray")
    db["crosses"].create_index([("AssignedTo", 1), ("Status", 1), ("TrayID", 1)], name="crosses_assigned_status_tray")
    db["crosses"].create_index([("User", 1), ("MaleUniqueID", 1)], name="crosses_user_male_uid")
    db["crosses"].create_index([("User", 1), ("FemaleUniqueID", 1)], name="crosses_user_female_uid")

    db["users"].create_index([("Username", 1)], name="users_username")
    db["users"].create_index([("ReportsTo", 1)], name="users_reports_to")

    db["activity"].create_index([("user", 1), ("timestamp", -1)], name="activity_user_timestamp")

    db["trays"].create_index([("User", 1), ("TrayID", 1)], name="trays_user_trayid")

    db["password_reset_tokens"].create_index([("TokenHash", 1)], name="password_reset_tokens_hash")

    db["operation_locks"].create_index("key", unique=True, name="operation_locks_key")
    db["operation_locks"].create_index("expires_at", expireAfterSeconds=0, name="operation_locks_expires_at")
    db["operation_locks"].create_index(
        [("status", 1), ("created_at", -1)], name="operation_locks_status_created"
    )
    db["operation_locks"].create_index(
        [("actor", 1), ("status", 1), ("created_at", -1)],
        name="operation_locks_actor_status_created",
    )

    db["marker_definitions"].create_index("Key", unique=True, name="marker_definitions_key")
    db["marker_images"].create_index("imageId", unique=True, name="marker_images_image_id")
    db["marker_images"].create_index("match.markerKeys", name="marker_images_marker_keys")
    db["marker_images"].create_index("sha256", name="marker_images_sha256")


def ping_database(db):
    """Return True when the MongoDB connection is healthy, else False."""
    try:
        db.command("ping")
        return True
    except Exception:
        return False

def reset_database(db):
    """
    Drop all collections in the database and recreate the necessary ones.
    
    Parameters:
    db: pymongo.database.Database
        The database instance.
    """
    # Drop all collections in the database
    for collection in db.list_collection_names():
        db.drop_collection(collection)
    
    # Create the necessary collections
    collections = [
        "users", "activity", "stocks", "crosses", "types", 
        "food_types", "provenances", "genesX", "genes2nd",
        "genes3rd", "genes4th", "species", "settings"
    ]
    
    for collection in collections:
        db.create_collection(collection)

    from flymanager.utils.mongo.helpers import clear_metadata_cache

    clear_metadata_cache()
    ensure_mongo_indexes(db)

def uid_exists(uid, db):
    """
    Check if a stock or cross with the given UniqueID exists in the MongoDB database.

    Parameters:
    uid: str
        The unique identifier of the stock or cross.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the UniqueID exists, False otherwise.
    """
    # Check if the UniqueID exists in the stocks collection or the crosses collection
    stocks_collection = db["stocks"]
    crosses_collection = db["crosses"]

    stock = stocks_collection.find_one({"UniqueID": uid}, {"_id": 1})
    cross = crosses_collection.find_one({"UniqueID": uid}, {"_id": 1})

    return stock is not None or cross is not None
