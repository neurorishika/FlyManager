import os
from pymongo import MongoClient
from dotenv import load_dotenv
import datetime
from hashlib import shake_256
from flymanager.utils.genetics import qc_genotype


# Load environment variables from .env file
load_dotenv()

def create_mongo_client():
    mongo_uri = os.getenv("MONGO_URI")
    client = MongoClient(mongo_uri)
    return client

def get_database(client):
    db_name = os.getenv("MONGO_DB_NAME")
    return client[db_name]

def reset_database(db):
    # Drop all collections in the database
    for collection in db.list_collection_names():
        db.drop_collection(collection)
    
    # Create the necessary collections
    db.create_collection("users")
    db.create_collection("activity")
    db.create_collection("stocks")
    db.create_collection("crosses")
    db.create_collection("types")
    db.create_collection("food_types")
    db.create_collection("provenances")
    db.create_collection("transgenesX")
    db.create_collection("transgenes2nd")
    db.create_collection("transgenes3rd")
    db.create_collection("transgenes4th")


# Authentication and User Management

def write_activity(user, activity, db):
    """
    Write an activity to the MongoDB collection.
    Parameters:
    user: str
        the username of the user
    activity: str
        the activity of the user
    db: pymongo.database.Database
        the database instance for MongoDB
    """
    # get timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # create the activity document
    activity_document = {
        "user": user,
        "timestamp": timestamp,
        "activity": activity
    }
    
    # insert the document into the activities collection
    db.activity.insert_one(activity_document)

def add_user(user, password, initials, db):
    """
    Add a user to the MongoDB database
    Parameters:
    user: str
        the username of the user
    password: str
        the password of the user
    initials: str
        the initials of the user
    db: pymongo.database.Database
        the MongoDB database instance
    Returns:
    bool
        True if the user was added, False otherwise
    """
    users_collection = db['users']
    
    # Check if the user already exists
    if users_collection.find_one({"Username": user}):
        return False
    
    # Hash the password
    hashed_password = shake_256((user + password).encode()).hexdigest(5)
    
    # Add the user document to the collection
    user_document = {
        "Username": user,
        "Password": hashed_password,
        "Initials": initials
    }
    
    users_collection.insert_one(user_document)
    
    # Log the activity
    write_activity(user, "User added", db)
    
    return True

def get_all_users(db):
    """
    Get all the users from the MongoDB collection.
    Parameters:
    db: pymongo.database.Database
        The database connection to the MongoDB database.
    Returns:
    clients: dict
        A dictionary of all the users and their hashed passwords.
    """
    # Access the users collection
    users_collection = db["users"]
    
    # Query all documents in the users collection
    users = users_collection.find()
    
    # Create a dictionary of {username: password}
    clients = {}
    for user in users:
        clients[user["Username"]] = user["Password"]
    
    return clients

def change_password(user, new_password, db):
    """
    Change the password of the user in the MongoDB database.
    Parameters:
    user: str
        the username of the user
    new_password: str
        the new password of the user
    db: pymongo.database.Database
        the MongoDB database instance
    Returns:
    bool
        True if the password was changed, False otherwise
    """

    users_collection = db['users']
    
    # Hash the new password
    hashed_password = shake_256((user + new_password).encode()).hexdigest(5)
    
    # Update the user document in the collection
    result = users_collection.update_one(
        {"Username": user},
        {"$set": {"Password": hashed_password}}
    )
    
    # Log the activity
    write_activity(user, "Password changed", db)
    
    return result.matched_count > 0

# User Data Management

def get_user_stocks(user, db):
    """
    Connect to the MongoDB collection for the given user's stocks.
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    Returns:
    stocks: list
        A list of dictionaries representing the user's stocks.
    """
    stock_collection = db["stocks"]
    filtered_stocks = stock_collection.find({"User": user})
    return list(filtered_stocks)

def get_user_initials(user, db):
    """
    Get the initials of the user
    Parameters:
    user: str
        the username of the user
    db: pymongo.database.Database
        the MongoDB database instance
    Returns:
    initials: str
        the initials of the user
    """
    users_collection = db['users']
    
    # Find the user document by username
    user_document = users_collection.find_one({"Username": user})
    
    if user_document:
        return user_document.get("Initials")
    else:
        return None

def get_user_crosses(user, db):
    """
    Connect to the MongoDB collection for the given user's crosses.
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    Returns:
    collection: pymongo.collection.Collection
        The collection representing the user's crosses.
    """
    collection_name = f"{user}_Cross"
    collection = db[collection_name]
    return collection

def get_user_activities(user, db):
    """
    Retrieve the user's activities from the MongoDB database.
    
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    list
        A list of dictionaries representing the user's activities.
    """
    activities_collection = db["activity"]
    
    # Find all activities where the 'username' field matches the given user
    user_activities = list(activities_collection.find({"username": user}))
    
    return user_activities

# Stock and Cross Management

def uid_exists(uid, db):
    """
    Check if a stock or cross with the given UID exists in the MongoDB database.
    
    Parameters:
    uid: str
        The unique identifier of the stock or cross.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    bool
        True if the UID exists, False otherwise.
    """
    # Check if the UID exists in the stocks collection or the crosses collection
    stocks_collection = db["stocks"]
    crosses_collection = db["crosses"]

    stock = stocks_collection.find_one({"UID": uid})
    cross = crosses_collection.find_one({"UID": uid})

    return stock is not None or cross is not None

def add_to_stock(user, properties, db):
    """
    Add a stock to the user's stock collection in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    properties: dict
        The properties of the stock.
        Properties:
            SourceID (required)
            Genotype (required)
            Name (required)
            AltReference (optional)
            Type (required)
            SeriesID (required)
            ReplicateID (required)
            TrayID (optional)
            TrayPosition (optional)
            Status (required)
            FoodType (optional)
            Provenance (optional)
            Comments (optional)
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    bool
        True if the stock was added, False otherwise.
    uid: str
        The unique identifier of the stock.
    """

    assert "SourceID" in properties, "SourceID is required"
    assert "Genotype" in properties, "Genotype is required"
    assert "Name" in properties, "Name is required"
    assert "Type" in properties, "Type is required"
    assert "SeriesID" in properties, "SeriesID is required"
    assert "ReplicateID" in properties, "ReplicateID is required"
    assert "Status" in properties, "Status is required"

    # make sure genotype meets the qc
    qc, genotype = qc_genotype(properties["Genotype"])

    if not qc:
        return False, genotype

    # create UID as a hash of the (User + Genotype + SeriesID + ReplicateID)
    uid = str(user) + str(properties["Genotype"]) + str(properties["SeriesID"]) + str(properties["ReplicateID"])
    uid = shake_256(uid.encode()).hexdigest(5)

    # make sure the UID is unique across all users
    while uid_exists(uid, db):
        print("UID already exists, generating a new one")
        uid = shake_256(uid.encode()).hexdigest(5)
    
    # get creation timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # create the document to insert
    stock_document = {
        "UID": uid,
        "User": user,
        "SourceID": properties["SourceID"],
        "Genotype": genotype,
        "Name": properties["Name"],
        "AltReference": properties.get("AltReference", ""),
        "Type": properties["Type"],
        "SeriesID": properties["SeriesID"],
        "ReplicateID": properties["ReplicateID"],
        "TrayID": properties.get("TrayID", ""),
        "TrayPosition": properties.get("TrayPosition", ""),
        "Status": properties["Status"],
        "FoodType": properties.get("FoodType", ""),
        "Provenance": properties.get("Provenance", ""),
        "Comments": properties.get("Comments", ""),
        "CreationDate": timestamp,
        "LastFlipDate": timestamp,
        "FlipLog": timestamp,
        "DataModifiedDate": timestamp,
        "ModificationLog": f"{timestamp} : Stock created"
    }

    # insert the document into the MongoDB collection
    stocks_collection = db["stocks"]
    stocks_collection.insert_one(stock_document)
    
    return True, uid

def get_stock(user, uid, db, admin_include=False):
    """
    Get the stock from the user's stock collection in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the stock.
    db: pymongo.database.Database
        The MongoDB database instance.
    admin_include: bool
        Whether to include admin stocks as well.
    
    Returns:
    stock: dict
        The properties of the stock.
    """

    # Define the user's collection
    stocks_collection = db["stocks"]

    # Try to find the stock in the user's collection
    stock = stocks_collection.find_one({"UID": uid, "User": user})
    
    # If not found and admin_include is True, search in the admin's collection
    if not stock and admin_include and user != "admin":
        stock = stocks_collection.find_one({"UID": uid, "User": "admin"})
    
    return stock

from pymongo import MongoClient

def flip_stock(user, uid, db, timestamp, new_status=None, added_comment=None):
    """
    Flip the status of the stock in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the stock.
    db: pymongo.database.Database
        The MongoDB database instance.
    timestamp: str
        The special timestamp to use.
    new_status: str
        The new status of the stock.
    added_comment: str
        The comment to add to the stock.
    """
    
    # Define the user's collection
    stocks_collection = db["stocks"]

    # Prepare the update fields
    update_fields = {}
    
    # Update the LastFlipDate and FlipLog
    ts = timestamp.replace('T', ' ')
    current_stock = stocks_collection.find_one({"UID": uid, "User": user})
    
    if current_stock:
        # Update LastFlipDate
        update_fields['LastFlipDate'] = ts

        # Update FlipLog
        flip_log = current_stock.get('FlipLog', '')
        update_fields['FlipLog'] = f"{ts}; {flip_log}" if flip_log else ts

        # Prepare the modification log
        modification_log_entries = []
        
        # If new status is provided and different from current, update it
        if new_status and current_stock.get('Status') != new_status:
            update_fields['Status'] = new_status
            update_fields['DataModifiedDate'] = ts
            modification_log_entries.append(f"{ts} : Status changed from {current_stock.get('Status')} to {new_status}")

        # If comment is provided, add it to the comments and the modification log
        if added_comment:
            comments = current_stock.get('Comments', '')
            update_fields['Comments'] = f"{added_comment}; {comments}" if comments else added_comment
            modification_log_entries.append(f"{ts} : Comments added: {added_comment}")

        # If there are modification log entries, concatenate them with the existing log
        if modification_log_entries:
            modification_log = current_stock.get('ModificationLog', '')
            new_modification_log = "; ".join(modification_log_entries)
            update_fields['ModificationLog'] = f"{new_modification_log}; {modification_log}" if modification_log else new_modification_log

        # Update the stock document in MongoDB
        stocks_collection.update_one(
            {"UID": uid, "User": user},
            {"$set": update_fields}
        )

def delete_stock(user, uid, db):
    """
    Delete a stock from the user's stock collection.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the stock.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    bool
        True if the stock was deleted, False if not found.
    """
    
    # Define the user's collection
    stocks_collection = db["stocks"]
    
    # Attempt to delete the stock
    result = stocks_collection.delete_one({"UID": uid, "User": user})
    
    # Check if any document was deleted
    if result.deleted_count > 0:
        return True
    else:
        return False

def edit_stock(user, uid, db, updates):
    """
    Edit specific fields of a stock in the user's stock collection.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the stock.
    db: pymongo.database.Database
        The MongoDB database instance.
    updates: dict
        A dictionary of the fields to update and their new values.
    
    Returns:
    bool
        True if the stock was updated, False if not found.
    """
    
    # Define the user's collection
    stocks_collection = db["stocks"]
    
    # Prepare the update fields and log the modification
    update_fields = {}
    modification_log_entries = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Loop through the updates and apply them
    for field, value in updates.items():
        update_fields[field] = value
        modification_log_entries.append(f"{timestamp} : {field} updated to {value}")
    
    # If there are updates, add to the ModificationLog and DataModifiedDate
    if modification_log_entries:
        current_stock = stocks_collection.find_one({"UID": uid, "User": user})
        if current_stock:
            modification_log = current_stock.get('ModificationLog', '')
            new_modification_log = "; ".join(modification_log_entries)
            update_fields['ModificationLog'] = f"{new_modification_log}; {modification_log}" if modification_log else new_modification_log
            update_fields['DataModifiedDate'] = timestamp
            
            # Update the stock document in MongoDB
            result = stocks_collection.update_one(
                {"UID": uid, "User": user},
                {"$set": update_fields}
            )
            
            return result.matched_count > 0
    
    return False

# Metadata Management
def get_metadata(metadata_type, db):
    """
    Get the metadata for a specific type from the MongoDB database.
    
    Parameters:
    metadata_type: str
        The type of metadata to retrieve.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    metadata: list
        A list of dictionaries representing the metadata.
    """
    assert metadata_type in ["types", "food_types", "provenances", "transgenesX", "transgenes2nd", 
                             "transgenes3rd", "transgenes4th"], "Invalid metadata type"
    metadata_collection = db[metadata_type]
    metadata = list(metadata_collection.find())
    return metadata

def add_metadata(metadata_type, metadata_value, db):
    """
    Add metadata to the MongoDB database.
    
    Parameters:
    metadata_type: str
        The type of metadata to add.
    metadata_value: str
        The value of the metadata to add.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    bool
        True if the metadata was added, False otherwise.
    """
    assert metadata_type in ["types", "food_types", "provenances", "transgenesX", "transgenes2nd",
                             "transgenes3rd", "transgenes4th"], "Invalid metadata type"
    
    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Check if the metadata already exists
    if metadata_collection.find_one({"Value": metadata_value}):
        return False
    
    # Add the metadata document to the collection
    metadata_document = {
        "Value": metadata_value
    }

    metadata_collection.insert_one(metadata_document)

    return True

def delete_metadata(metadata_type, metadata_value, db):
    """
    Delete metadata from the MongoDB database.
    
    Parameters:
    metadata_type: str
        The type of metadata to delete.
    metadata_value: str
        The value of the metadata to delete.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    bool
        True if the metadata was deleted, False otherwise.
    """
    assert metadata_type in ["types", "food_types", "provenances", "transgenesX", "transgenes2nd",
                             "transgenes3rd", "transgenes4th"], "Invalid metadata type"
    
    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Attempt to delete the metadata
    result = metadata_collection.delete_one({"Value": metadata_value})

    # Check if any document was deleted
    if result.deleted_count > 0:
        return True
    else:
        return False
    
def edit_metadata(metadata_type, old_value, new_value, db):
    """
    Edit metadata in the MongoDB database.
    
    Parameters:
    metadata_type: str
        The type of metadata to edit.
    old_value: str
        The old value of the metadata.
    new_value: str
        The new value of the metadata.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the metadata was updated, False otherwise.
    """
    assert metadata_type in ["types", "food_types", "provenances", "transgenesX", "transgenes2nd",
                             "transgenes3rd", "transgenes4th"], "Invalid metadata type"
    
    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Attempt to update the metadata
    result = metadata_collection.update_one(
        {"Value": old_value},
        {"$set": {"Value": new_value}}
    )

    return result.matched_count > 0














