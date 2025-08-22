import datetime
from hashlib import shake_256

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
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # create the activity document
    activity_document = {
        "user": user,
        "timestamp": timestamp,
        "activity": activity
    }
    
    # insert the document into the activities collection
    db.activity.insert_one(activity_document)