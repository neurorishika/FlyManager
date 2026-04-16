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

    stock = stocks_collection.find_one({"UniqueID": uid})
    cross = crosses_collection.find_one({"UniqueID": uid})

    return stock is not None or cross is not None