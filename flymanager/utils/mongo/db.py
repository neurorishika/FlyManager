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


def ensure_mongo_indexes(db):
    """Create the indexes used by stock/cross access, tray, and auth query patterns."""
    db["stocks"].create_index([("User", 1), ("UniqueID", 1)], name="stocks_user_uid")
    db["stocks"].create_index([("AssignedTo", 1), ("UniqueID", 1)], name="stocks_assigned_uid")
    db["stocks"].create_index([("User", 1), ("Status", 1), ("TrayID", 1)], name="stocks_user_status_tray")
    db["stocks"].create_index([("AssignedTo", 1), ("Status", 1), ("TrayID", 1)], name="stocks_assigned_status_tray")

    db["crosses"].create_index([("User", 1), ("UniqueID", 1)], name="crosses_user_uid")
    db["crosses"].create_index([("AssignedTo", 1), ("UniqueID", 1)], name="crosses_assigned_uid")
    db["crosses"].create_index([("User", 1), ("Status", 1), ("TrayID", 1)], name="crosses_user_status_tray")
    db["crosses"].create_index([("AssignedTo", 1), ("Status", 1), ("TrayID", 1)], name="crosses_assigned_status_tray")

    db["users"].create_index([("Username", 1)], name="users_username")
    db["users"].create_index([("ReportsTo", 1)], name="users_reports_to")

    db["activity"].create_index([("user", 1), ("timestamp", -1)], name="activity_user_timestamp")

    db["trays"].create_index([("User", 1), ("TrayID", 1)], name="trays_user_trayid")

    db["password_reset_tokens"].create_index([("TokenHash", 1)], name="password_reset_tokens_hash")

    db["operation_locks"].create_index("key", unique=True, name="operation_locks_key")
    db["operation_locks"].create_index("expires_at", expireAfterSeconds=0, name="operation_locks_expires_at")


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