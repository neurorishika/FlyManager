import os
from pymongo import MongoClient
from dotenv import load_dotenv
import datetime
from hashlib import shake_256
from flymanager.utils.genetics import qc_genotype
from flymanager.utils.utils import clean_log_entry


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
    db.create_collection("genesX")
    db.create_collection("genes2nd")
    db.create_collection("genes3rd")
    db.create_collection("genes4th")


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
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
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

def get_user_flip_days(user, db):
    """
    Get the preferred flip days of the user
    Parameters:
    user: str
        the username of the user
    db: pymongo.database.Database
        the MongoDB database instance
    Returns:
    flip_days: list
        the preferred flip days of the user
    """
    users_collection = db['users']
    
    # Find the user document by username
    user_document = users_collection.find_one({"Username": user})
    
    if user_document:
        return user_document.get("FlipDays")
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
    crosses_collection = db["crosses"]
    filtered_crosses = crosses_collection.find({"User": user})
    return list(filtered_crosses)

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

def get_all_genotypes(user, db):
    """
    Get all the genotypes of the user's stocks.
    
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    genotypes: list
        A list of all the unique genotypes.
    """
    stocks_collection = db["stocks"]
    
    # Find all the stocks and crosses of the user
    user_stocks = stocks_collection.find({"User": user})
    
    # Extract the genotypes from the stocks and crosses
    stock_genotypes = [stock["Genotype"] for stock in user_stocks]
    
    # Remove duplicates and sort the genotypes
    genotypes = list(set(stock_genotypes))
    
    return genotypes

# Stock and Cross Management

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

### Stock Management

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
            VialLifetime (required)
            FlipFrequency (required)
            DevelopmentalTime (required)
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
    assert "VialLifetime" in properties, "VialLifetime is required"
    assert "FlipFrequency" in properties, "FlipFrequency is required"
    assert "DevelopmentalTime" in properties, "DevelopmentalTime is required"

    # make sure genotype meets the qc
    qc, genotype = qc_genotype(properties["Genotype"])

    if not qc:
        return False, genotype

    # create UniqueID as a hash of the (User + Genotype + SeriesID + ReplicateID)
    uid = str(user) + str(properties["Genotype"]) + str(properties["SeriesID"]) + str(properties["ReplicateID"])
    uid = shake_256(uid.encode()).hexdigest(5)

    # make sure the UniqueID is unique across all users
    while uid_exists(uid, db):
        print("UniqueID already exists, generating a new one")
        uid = shake_256(uid.encode()).hexdigest(5)
    
    # get creation timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # create the document to insert
    stock_document = {
        "UniqueID": uid,
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
        "VialLifetime": properties["VialLifetime"],
        "FlipFrequency": properties["FlipFrequency"],
        "DevelopmentalTime": properties["DevelopmentalTime"],
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
    stock = stocks_collection.find_one({"UniqueID": uid, "User": user})
    
    # If not found and admin_include is True, search in the admin's collection
    if not stock and admin_include and user != "admin":
        stock = stocks_collection.find_one({"UniqueID": uid, "User": "admin"})
    
    return stock



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
    print(f"Flipping stock {uid} for user {user}")
    # Prepare the update fields
    update_fields = {}
    
    # Update the LastFlipDate and FlipLog
    ts = timestamp.replace('T', ' ')
    current_stock = stocks_collection.find_one({"UniqueID": uid, "User": user})

    # If the stock exists
    if current_stock:
        # Update LastFlipDate
        update_fields['LastFlipDate'] = ts

        # Update FlipLog and CurrentAliveVials
        flip_log = current_stock.get('FlipLog', '')
        currently_alive_vials = current_stock.get('CurrentlyAliveVials', '')

        if "," in flip_log:
            last_vial = int(flip_log.split(",")[0].strip()[1:])
            last_vial += 1
            flip_log = f"V{last_vial}, {ts}; {flip_log}"
            currently_alive_vials = f"{currently_alive_vials}, V{last_vial}"
        else:
            flip_log = f"V1, {ts}"
            currently_alive_vials = "V1"

        update_fields['FlipLog'] = flip_log
        update_fields['CurrentlyAliveVials'] = currently_alive_vials
        
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
            {"UniqueID": uid, "User": user},
            {"$set": update_fields}
        )

    # get the updated stock
    updated_stock = stocks_collection.find_one({"UniqueID": uid, "User": user})
    update_stock_vials(updated_stock, user, db)



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
    result = stocks_collection.delete_one({"UniqueID": uid, "User": user})
    
    # Check if any document was deleted
    if result.deleted_count > 0:
        return True
    else:
        return False

def edit_stock(user, uid, db, updates, log_activity=True, refresh_vials=True):
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
    log_activity: bool
        Whether to log the activity of the stock update.
    refresh_vials: bool
        Whether to refresh the vials of the stock.
    
    Returns:
    bool
        True if the stock was updated, False if not found.
    """
    
    # Define the user's collection
    stocks_collection = db["stocks"]
    
    # Prepare the update fields and log the modification
    update_fields = {}
    modification_log_entries = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Loop through the updates and apply them
    for field, value in updates.items():
        update_fields[field] = value
        modification_log_entries.append(f"{timestamp} : {field} updated to {value}")
    
    # If there are updates, add to the ModificationLog and DataModifiedDate
    if modification_log_entries:
        current_stock = stocks_collection.find_one({"UniqueID": uid, "User": user})
        if current_stock:
            if log_activity:
                modification_log = current_stock.get('ModificationLog', '')
                new_modification_log = "; ".join(modification_log_entries)
                update_fields['ModificationLog'] = f"{new_modification_log}; {modification_log}" if modification_log else new_modification_log
                update_fields['DataModifiedDate'] = timestamp
            
            # Update the stock document in MongoDB
            result = stocks_collection.update_one(
                {"UniqueID": uid, "User": user},
                {"$set": update_fields}
            )

            # Refresh the vials if requested
            if refresh_vials:
                update_stock_vials(current_stock, user, db)
            
            return result.matched_count > 0
    
    return False


def update_stock_vials(stock, username, db):
    """
    Refresh the stock vials based on the flip log.

    Parameters:
    stock : dict
        The stock dictionary.
    username : str
        The username of the user.
    db : pymongo.database.Database
        The database object.

    Returns:
    bool
        True if the stock was updated successfully, False otherwise.
    """
    uid = stock["UniqueID"]

    # check if the stock doesnt have the key "CurrentlyAliveVials"
    if "CurrentlyAliveVials" not in stock:
        assert all([x in stock for x in ["FlipLog","VialLifetime","FlipFrequency","DevelopmentalTime"]]), "Stock must have the keys FlipLog, VialLifetime, and FlipFrequency"
        
        # get the stock details
        flipLog = stock["FlipLog"]
        vialLifetime = float(stock["VialLifetime"])
        flipFrequency = float(stock["FlipFrequency"])
        developmentalTime = float(stock["DevelopmentalTime"])
        # split the flip log into a list by ";"
        flipLog = flipLog.split(";")
        _, flip_dates = zip(*[clean_log_entry(flip) for flip in flipLog])

        # reverse the order of the vials and dates
        flip_dates = flip_dates[::-1]
            
        vials = ["V{}".format(i) for i in range(1,len(flip_dates)+1)]
        next_flip_dates = [date+datetime.timedelta(days=flipFrequency) for date in flip_dates]
        next_eclosion_dates = [date+datetime.timedelta(days=developmentalTime) for date in flip_dates]

        # recreate a new flip log
        flipLog = [f"{vial}, {date.strftime('%Y-%m-%d %H:%M')}" for vial, date in zip(vials[::-1],flip_dates[::-1])]
        flipLog = "; ".join(flipLog)

        # remove HH:MM:SS from the dates
        flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in flip_dates]
        next_flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_flip_dates]
        next_eclosion_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_eclosion_dates]

        # determine the dead vials
        currently_alive_vials = vials.copy()
        to_delete = []
        for vial, flip_date, next_flip_date in zip(vials,flip_dates,next_flip_dates):
            death_date = flip_date + datetime.timedelta(days=vialLifetime)
            # if death date has passed and there is atleast one flip AFTER the scheduled death date
            if datetime.datetime.now() > death_date and any([date >= next_flip_date for date in flip_dates]):
                # get the index of the vial
                index = currently_alive_vials.index(vial)
                to_delete.append(index)
                print(f"Vial {vial} for stock {uid} has died")

        # remove the dead vials
        currently_alive_vials = [vial for i,vial in enumerate(currently_alive_vials) if i not in to_delete] 
        next_flip_dates = [date for i,date in enumerate(next_flip_dates) if i not in to_delete]
        next_eclosion_dates = [date for i,date in enumerate(next_eclosion_dates) if i not in to_delete]

        # get the last flip date
        last_flip_date = datetime.datetime.strptime(stock["LastFlipDate"], "%Y-%m-%d %H:%M")
        # remove HH:MM:SS from the last flip date
        last_flip_date = last_flip_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # remove all next flip dates that are before the last flip date + 1 day to account for early flips
        next_flip_dates = [date for date in next_flip_dates if date > last_flip_date + datetime.timedelta(days=1)]


        # define the currently alive vials
        currently_alive_vials = ", ".join(currently_alive_vials)
        # define the next flip dates
        next_flip_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_flip_dates])
        # define the next eclosion dates
        next_eclosion_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_eclosion_dates])
        # update the stock
        update_properties = {
            "FlipLog":flipLog,
            "CurrentlyAliveVials":currently_alive_vials,
            "NextFlipDates":next_flip_dates,
            "NextEclosionDates":next_eclosion_dates
        }
        # edit the stock
        success = edit_stock(username, uid, db, update_properties, log_activity=False)
        
    else:
        # get all flip dates
        flipLog = stock["FlipLog"]
        flipLog = flipLog.split(";")
        date_map = {}
        flip_dates = []

        for flip in flipLog:
            vial, date = clean_log_entry(flip)
            date_map[vial] = date
            flip_dates.append(date)

        # get the currently alive vials
        currently_alive_vials = stock["CurrentlyAliveVials"].split(", ")
        # keep only the alive vials
        dates = [date_map[vial] for vial in currently_alive_vials]
        # get the next flip dates
        flipFrequency = float(stock["FlipFrequency"])
        next_flip_dates = [date+datetime.timedelta(days=flipFrequency) for date in dates]
        # get the next eclosion dates
        developmentalTime = float(stock["DevelopmentalTime"])
        next_eclosion_dates = [date+datetime.timedelta(days=developmentalTime) for date in dates]

        # remove HH:MM:SS from the dates
        flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in flip_dates]
        next_flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_flip_dates]
        next_eclosion_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_eclosion_dates]

        # determine the dead vials
        vialLifetime = float(stock["VialLifetime"])
        to_delete = []
        for vial, flip_date, next_flip_date in zip(currently_alive_vials,dates,next_flip_dates):
            death_date = flip_date + datetime.timedelta(days=vialLifetime)
            # if death date has passed and there is atleast one flip AFTER the scheduled death date
            if datetime.datetime.now() > death_date and any([date >= next_flip_date for date in flip_dates]):
                # get the index of the vial
                index = currently_alive_vials.index(vial)
                to_delete.append(index)
                print(f"Vial {vial} for stock {uid} has died")
        
        # remove the dead vials
        currently_alive_vials = [vial for i,vial in enumerate(currently_alive_vials) if i not in to_delete]
        next_flip_dates = [date for i,date in enumerate(next_flip_dates) if i not in to_delete]
        next_eclosion_dates = [date for i,date in enumerate(next_eclosion_dates) if i not in to_delete]

        # get the last flip date
        last_flip_date = datetime.datetime.strptime(stock["LastFlipDate"], "%Y-%m-%d %H:%M")
        # remove HH:MM:SS from the last flip date
        last_flip_date = last_flip_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # remove all next flip dates that are before the last flip date + 1 day to account for early flips
        next_flip_dates = [date for date in next_flip_dates if date > last_flip_date + datetime.timedelta(days=1)]
        
        # define the currently alive vials
        currently_alive_vials = ", ".join(currently_alive_vials)
        # define the next flip dates
        next_flip_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_flip_dates])
        # define the next eclosion dates
        next_eclosion_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_eclosion_dates])
        # update the stock
        update_properties = {
            "CurrentlyAliveVials":currently_alive_vials,
            "NextFlipDates":next_flip_dates,
            "NextEclosionDates":next_eclosion_dates
        }
        # edit the stock
        success = edit_stock(username, uid, db, update_properties, log_activity=False, refresh_vials=False)
    return success
    

### Cross Management


def add_to_cross(user, properties, db):
    """
    Add a cross to the user's cross collection in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    properties: dict
        The properties of the cross.
        Properties:
            MaleUniqueID (required)
            FemaleUniqueID (required)
            MaleGenotype (required)
            FemaleGenotype (required)
            Name (required)
            FoodType (required)
            VialLifetime (required)
            FlipFrequency (required)
            DevelopmentalTime (required)
            TrayID (optional)
            TrayPosition (optional)
            Status (required)
            Comments (optional)
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the cross was added, False otherwise.
    uid: str
        The unique identifier of the cross.
    """

    # Check for required fields
    assert "MaleUniqueID" in properties, "MaleUniqueID is required"
    assert "FemaleUniqueID" in properties, "FemaleUniqueID is required"
    assert "MaleGenotype" in properties, "MaleGenotype is required"
    assert "FemaleGenotype" in properties, "FemaleGenotype is required"
    assert "Name" in properties, "Name is required"
    assert "Status" in properties, "Status is required"
    assert "FoodType" in properties, "FoodType is required"
    assert "VialLifetime" in properties, "VialLifetime is required"
    assert "FlipFrequency" in properties, "FlipFrequency is required"
    assert "DevelopmentalTime" in properties, "DevelopmentalTime is required"

    # Create a UniqueID for the cross based on Male and Female UniqueID + User + Name
    uid = str(user) + str(properties["MaleUniqueID"]) + str(properties["FemaleUniqueID"]) + str(properties["Name"])
    uid = shake_256(uid.encode()).hexdigest(5)

    # Ensure the UniqueID is unique
    while uid_exists(uid, db):
        print("UniqueID already exists, generating a new one")
        uid = shake_256(uid.encode()).hexdigest(5)

    # Get the current timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Create the document to insert
    cross_document = {
        "UniqueID": uid,
        "User": user,
        "MaleUniqueID": properties["MaleUniqueID"],
        "FemaleUniqueID": properties["FemaleUniqueID"],
        "MaleGenotype": properties["MaleGenotype"],
        "FemaleGenotype": properties["FemaleGenotype"],
        "Name": properties["Name"],
        "FoodType": properties["FoodType"],
        "TrayID": properties.get("TrayID", ""),
        "TrayPosition": properties.get("TrayPosition", ""),
        "VialLifetime": properties["VialLifetime"],
        "FlipFrequency": properties["FlipFrequency"],
        "DevelopmentalTime": properties["DevelopmentalTime"],
        "Status": properties["Status"],
        "Comments": properties.get("Comments", ""),
        "CreationDate": timestamp,
        "DataModifiedDate": timestamp,
        "ModificationLog": f"{timestamp} : Cross created"
    }

    # Insert the document into the MongoDB collection
    crosses_collection = db["crosses"]
    crosses_collection.insert_one(cross_document)

    return True, uid

def get_cross(user, uid, db, admin_include=False):
    """
    Get the cross from the user's cross collection in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the cross.
    db: pymongo.database.Database
        The MongoDB database instance.
    admin_include: bool
        Whether to include admin crosses as well.
    
    Returns:
    cross: dict
        The properties of the cross.
    """

    # Define the user's collection
    crosses_collection = db["crosses"]

    # Try to find the cross in the user's collection
    cross = crosses_collection.find_one({"UniqueID": uid, "User": user})
    
    # If not found and admin_include is True, search in the admin's collection
    if not cross and admin_include and user != "admin":
        cross = crosses_collection.find_one({"UniqueID": uid, "User": "admin"})
    
    return cross

def flip_cross(user, uid, db, timestamp, new_status=None, added_comment=None):
    """
    Flip the status of the cross in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the cross.
    db: pymongo.database.Database
        The MongoDB database instance.
    timestamp: str
        The special timestamp to use.
    new_status: str
        The new status of the cross.
    added_comment: str
        The comment to add to the cross.
    """
    
    # Define the user's collection
    crosses_collection = db["crosses"]

    # Prepare the update fields
    update_fields = {}
    
    # Update the LastFlipDate and FlipLog
    ts = timestamp.replace('T', ' ')
    current_cross = crosses_collection.find_one({"UniqueID": uid, "User": user})

    if current_cross:
        # Update LastFlipDate
        update_fields['LastFlipDate'] = ts

        # Update FlipLog and CurrentAliveVials
        flip_log = current_cross.get('FlipLog', '')
        currently_alive_vials = current_cross.get('CurrentlyAliveVials', '')

        if "," in flip_log:
            last_vial = int(flip_log.split(",")[0].strip()[1:])
            last_vial += 1
            flip_log = f"V{last_vial}, {ts}; {flip_log}"
            currently_alive_vials = f"{currently_alive_vials}, V{last_vial}"
        else:
            flip_log = f"V1, {ts}"
            currently_alive_vials = "V1"

        update_fields['FlipLog'] = flip_log
        update_fields['CurrentlyAliveVials'] = currently_alive_vials

        # Prepare the modification log
        modification_log_entries = []
        
        # If new status is provided and different from current, update it
        if new_status and current_cross.get('Status') != new_status:
            update_fields['Status'] = new_status
            update_fields['DataModifiedDate'] = ts
            modification_log_entries.append(f"{ts} : Status changed from {current_cross.get('Status')} to {new_status}")

        # If comment is provided, add it to the comments and the modification log
        if added_comment:
            comments = current_cross.get('Comments', '')
            update_fields['Comments'] = f"{added_comment}; {comments}" if comments else added_comment
            modification_log_entries.append(f"{ts} : Comments added: {added_comment}")

        # If there are modification log entries, concatenate them with the existing log
        if modification_log_entries:
            modification_log = current_cross.get('ModificationLog', '')
            new_modification_log = "; ".join(modification_log_entries)
            update_fields['ModificationLog'] = f"{new_modification_log}; {modification_log}" if modification_log else new_modification_log

        # Update the cross document in MongoDB
        crosses_collection.update_one(
            {"UniqueID": uid, "User": user},
            {"$set": update_fields}
        )

    # get the updated cross
    updated_cross = crosses_collection.find_one({"UniqueID": uid, "User": user})
    update_cross_vials(updated_cross, user, db)

def delete_cross(user, uid, db):
    """
    Delete a cross from the user's cross collection.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the cross.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    bool
        True if the cross was deleted, False if not found.
    """
    
    # Define the user's collection
    crosses_collection = db["crosses"]
    
    # Attempt to delete the cross
    result = crosses_collection.delete_one({"UniqueID": uid, "User": user})
    
    # Check if any document was deleted
    if result.deleted_count > 0:
        return True
    else:
        return False

def edit_cross(user, uid, db, updates, log_activity=True, refresh_vials=True):
    """
    Edit specific fields of a cross in the user's cross collection.
    
    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the cross.
    db: pymongo.database.Database
        The MongoDB database instance.
    updates: dict
        A dictionary of the fields to update and their new values.
    log_activity: bool
        Whether to log the activity of the cross update.
    refresh_vials: bool
        Whether to refresh the vials of the cross.
    
    Returns:
    bool
        True if the cross was updated, False if not found.
    """
    
    # Define the user's collection
    crosses_collection = db["crosses"]
    
    # Prepare the update fields and log the modification
    update_fields = {}
    modification_log_entries = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Loop through the updates and apply them
    for field, value in updates.items():
        update_fields[field] = value
        modification_log_entries.append(f"{timestamp} : {field} updated to {value}")
    
    # If there are updates, add to the ModificationLog and DataModifiedDate
    if modification_log_entries:
        current_cross = crosses_collection.find_one({"UniqueID": uid, "User": user})
        if current_cross:
            if log_activity:
                modification_log = current_cross.get('ModificationLog', '')
                new_modification_log = "; ".join(modification_log_entries)
                update_fields['ModificationLog'] = f"{new_modification_log}; {modification_log}" if modification_log else new_modification_log
                update_fields['DataModifiedDate'] = timestamp
            
            # Update the cross document in MongoDB
            result = crosses_collection.update_one(
                {"UniqueID": uid, "User": user},
                {"$set": update_fields}
            )

            # update the cross vials if requested
            if refresh_vials:
                update_cross_vials(current_cross, user, db)
            
            return result.matched_count > 0
    
    return False

def update_cross_vials(cross, username, db):
    """
    Refresh the cross vials based on the flip log.

    Parameters:
    cross : dict
        The cross dictionary.   
    username : str
        The username of the user.
    db : pymongo.database.Database
        The database object.

    Returns:
    bool
        True if the cross was updated successfully, False otherwise.
    """
    uid = cross["UniqueID"]

    # check if the cross doesnt have the key "CurrentlyAliveVials"
    if "CurrentlyAliveVials" not in cross:
        assert all([x in cross for x in ["FlipLog","VialLifetime","FlipFrequency","DevelopmentalTime"]]), "Stock must have the keys FlipLog, VialLifetime, and FlipFrequency"
        
        # get the cross details
        flipLog = cross["FlipLog"]
        vialLifetime = float(cross["VialLifetime"])
        flipFrequency = float(cross["FlipFrequency"])
        developmentalTime = float(cross["DevelopmentalTime"])
        # split the flip log into a list by ";"
        flipLog = flipLog.split(";")
        _, flip_dates = zip(*[clean_log_entry(flip) for flip in flipLog])

        # reverse the order of the vials and dates
        flip_dates = flip_dates[::-1]
            
        vials = ["V{}".format(i) for i in range(1,len(flip_dates)+1)]
        next_flip_dates = [date+datetime.timedelta(days=flipFrequency) for date in flip_dates]
        next_eclosion_dates = [date+datetime.timedelta(days=developmentalTime) for date in flip_dates]

        # recreate a new flip log
        flipLog = [f"{vial}, {date.strftime('%Y-%m-%d %H:%M')}" for vial, date in zip(vials[::-1],flip_dates[::-1])]
        flipLog = "; ".join(flipLog)

        # remove HH:MM:SS from the dates
        flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in flip_dates]
        next_flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_flip_dates]
        next_eclosion_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_eclosion_dates]

        # determine the dead vials
        currently_alive_vials = vials.copy()
        to_delete = []
        for vial, flip_date, next_flip_date in zip(vials,flip_dates,next_flip_dates):
            death_date = flip_date + datetime.timedelta(days=vialLifetime)
            # if death date has passed and there is atleast one flip AFTER the scheduled death date
            if datetime.datetime.now() > death_date and any([date >= next_flip_date for date in flip_dates]):
                # get the index of the vial
                index = currently_alive_vials.index(vial)
                to_delete.append(index)
        # remove the dead vials
        currently_alive_vials = [vial for i,vial in enumerate(currently_alive_vials) if i not in to_delete] 
        next_flip_dates = [date for i,date in enumerate(next_flip_dates) if i not in to_delete]
        next_eclosion_dates = [date for i,date in enumerate(next_eclosion_dates) if i not in to_delete]

        # get the last flip date
        last_flip_date = datetime.datetime.strptime(cross["LastFlipDate"], "%Y-%m-%d %H:%M")
        # remove HH:MM:SS from the last flip date
        last_flip_date = last_flip_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # remove all next flip dates that are before the last flip date + 1 day to account for early flips
        next_flip_dates = [date for date in next_flip_dates if date > last_flip_date + datetime.timedelta(days=1)]
        
        # define the currently alive vials
        currently_alive_vials = ", ".join(currently_alive_vials)
        # define the next flip dates
        next_flip_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_flip_dates])
        # define the next eclosion dates
        next_eclosion_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_eclosion_dates])
        # update the cross
        update_properties = {
            "FlipLog":flipLog,
            "CurrentlyAliveVials":currently_alive_vials,
            "NextFlipDates":next_flip_dates,
            "NextEclosionDates":next_eclosion_dates
        }
        # edit the cross
        success = edit_cross(username, uid, db, update_properties, log_activity=False)
        
    else:
        # get all flip dates
        flipLog = cross["FlipLog"]
        flipLog = flipLog.split(";")
        date_map = {}
        flip_dates = []
        for flip in flipLog:
            vial, date = clean_log_entry(flip)
            date_map[vial] = date
            flip_dates.append(date)

        # get the currently alive vials
        currently_alive_vials = cross["CurrentlyAliveVials"].split(", ")
        # keep only the alive vials
        dates = [date_map[vial] for vial in currently_alive_vials]
        # get the next flip dates
        flipFrequency = float(cross["FlipFrequency"])
        next_flip_dates = [date+datetime.timedelta(days=flipFrequency) for date in dates]
        # get the next eclosion dates
        developmentalTime = float(cross["DevelopmentalTime"])
        next_eclosion_dates = [date+datetime.timedelta(days=developmentalTime) for date in dates]

        # remove HH:MM:SS from the dates
        flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in flip_dates]
        next_flip_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_flip_dates]
        next_eclosion_dates = [date.replace(hour=0, minute=0, second=0, microsecond=0) for date in next_eclosion_dates]

        # determine the dead vials
        vialLifetime = float(cross["VialLifetime"])
        to_delete = []
        for vial, flip_date, next_flip_date in zip(currently_alive_vials,dates,next_flip_dates):
            death_date = flip_date + datetime.timedelta(days=vialLifetime)
            # if death date has passed and there is atleast one flip AFTER the scheduled death date
            if datetime.datetime.now() > death_date and any([date >= next_flip_date for date in flip_dates]):
                # get the index of the vial
                index = currently_alive_vials.index(vial)
                to_delete.append(index)
                
        # remove the dead vials
        currently_alive_vials = [vial for i,vial in enumerate(currently_alive_vials) if i not in to_delete]
        next_flip_dates = [date for i,date in enumerate(next_flip_dates) if i not in to_delete]
        next_eclosion_dates = [date for i,date in enumerate(next_eclosion_dates) if i not in to_delete]

        # get the last flip date
        last_flip_date = datetime.datetime.strptime(cross["LastFlipDate"], "%Y-%m-%d %H:%M")
        # remove HH:MM:SS from the last flip date
        last_flip_date = last_flip_date.replace(hour=0, minute=0, second=0, microsecond=0)

        # remove all next flip dates that are before the last flip date  + 1 day to account for early flips
        next_flip_dates = [date for date in next_flip_dates if date > last_flip_date + datetime.timedelta(days=1)]
        
        # define the currently alive vials
        currently_alive_vials = ", ".join(currently_alive_vials)
        # define the next flip dates
        next_flip_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_flip_dates])
        # define the next eclosion dates
        next_eclosion_dates = ", ".join([date.strftime('%Y-%m-%d') for date in next_eclosion_dates])
        # update the cross
        update_properties = {
            "CurrentlyAliveVials":currently_alive_vials,
            "NextFlipDates":next_flip_dates,
            "NextEclosionDates":next_eclosion_dates
        }
        # edit the cross
        success = edit_cross(username, uid, db, update_properties, log_activity=False, refresh_vials=False)
    return success
    

### Metadata Management

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
    assert metadata_type in ["types", "food_types", "provenances", "genesX", "genes2nd", 
                             "genes3rd", "genes4th"], "Invalid metadata type"
    metadata_collection = db[metadata_type]
    metadata = list(metadata_collection.find())
    values = [m["Value"] for m in metadata]
    return values

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
    assert metadata_type in ["types", "food_types", "provenances", "genesX", "genes2nd",
                             "genes3rd", "genes4th"], "Invalid metadata type"
    
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
    assert metadata_type in ["types", "food_types", "provenances", "genesX", "genes2nd",
                             "genes3rd", "genes4th"], "Invalid metadata type"
    
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
    assert metadata_type in ["types", "food_types", "provenances", "genesX", "genes2nd",
                             "genes3rd", "genes4th"], "Invalid metadata type"
    
    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Attempt to update the metadata
    result = metadata_collection.update_one(
        {"Value": old_value},
        {"$set": {"Value": new_value}}
    )

    return result.matched_count > 0

### Special Utility Functions

def get_flip_in(item):
    """
    Get the flip in for a stock or cross.
    """
    vals = []
    for val in item["NextFlipDates"].split(", "):
        try:
            next_time = datetime.datetime.strptime(val, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            now = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            vals.append(round((next_time - now).days))
        except:
            vals.append("N/A")
    return ", ".join([str(val) for val in vals]) + " days"

def get_eclosion_in(item):
    """
    Get the eclosion in for a stock or cross.
    """
    vals = []
    for val in item["NextEclosionDates"].split(", "):
        try:
            next_time = datetime.datetime.strptime(val, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
            now = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            vals.append(round((next_time - now).days))
        except:
            vals.append("N/A")
    return ", ".join([str(val) for val in vals]) + " days"