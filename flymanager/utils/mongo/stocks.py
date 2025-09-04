import datetime
import math
from hashlib import shake_256
from flymanager.utils.genetics import qc_genotype
from flymanager.utils.utils import clean_log_entry
from flymanager.app.settings import (
    BASE_STOCK_PROPERTIES,
    REQUIRED_STOCK_PROPERTIES,
    DEFAULT_STOCK_PROPERTY_VALUES,
    OPTIONAL_STOCK_PROPERTIES,
)


def add_to_stock(user, properties, db):
    """
    Add a stock to the user's stock collection in MongoDB.

    Parameters:
    user: str
        The username of the user.
    properties: dict
        The properties of the stock.
        Properties:
            SEE REQUIRED_STOCK_PROPERTIES and OPTIONAL_STOCK_PROPERTIES
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the stock was added, False otherwise.
    uid: str
        The unique identifier of the stock.
    """

    for prop in REQUIRED_STOCK_PROPERTIES + BASE_STOCK_PROPERTIES:
        if prop not in properties:
            raise ValueError(f"{prop} is required")

    # make sure genotype meets the qc
    qc, genotype = qc_genotype(properties["Genotype"])

    if not qc:
        return False, genotype

    # create UniqueID as a hash of the (User + Genotype + SeriesID + ReplicateID)
    uid = (
        str(user)
        + str(properties["Genotype"])
        + str(properties["SeriesID"])
        + str(properties["ReplicateID"])
    )

    # Import here to avoid circular imports
    from flymanager.utils.mongo.db import uid_exists

    # make sure the UniqueID is unique across all users
    uid = shake_256(uid.encode()).hexdigest(5)
    while uid_exists(uid, db):
        print("UniqueID already exists, generating a new one")
        uid = shake_256(uid.encode()).hexdigest(5)

    # get creation timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # create the document to insert
    stock_document = {
        "UniqueID": uid,
        "User": user,
        "Genotype": genotype,
        "Name": properties["Name"],
        "TrayID": "",
        "TrayPosition": "",
        "CreationDate": timestamp,
        "LastFlipDate": timestamp,
        "FlipLog": timestamp,
        "DataModifiedDate": timestamp,
        "ModificationLog": f"{timestamp} : Stock created",
    }

    for prop in REQUIRED_STOCK_PROPERTIES:
        stock_document[prop] = properties[prop]

    for prop in OPTIONAL_STOCK_PROPERTIES:
        stock_document[prop] = properties.get(prop, "")

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
    timestamp: str or datetime
        The special timestamp to use.
    new_status: str
        The new status of the stock.
    added_comment: str
        The comment to add to the stock.
    """

    # Ensure timestamp is a string in ISO format
    if isinstance(timestamp, datetime.datetime):
        timestamp = timestamp.isoformat()
    elif not isinstance(timestamp, str):
        raise TypeError("timestamp must be a string or datetime object")

    # Replace 'T' with a space for compatibility
    ts = timestamp.replace("T", " ")

    # Define the user's collection
    stocks_collection = db["stocks"]
    print(f"Flipping stock {uid} for user {user}")
    # Prepare the update fields
    update_fields = {}

    # Update the LastFlipDate and FlipLog
    current_stock = stocks_collection.find_one({"UniqueID": uid, "User": user})

    # If the stock exists
    if current_stock:
        # Update LastFlipDate
        update_fields["LastFlipDate"] = ts

        # Update FlipLog and CurrentAliveVials
        flip_log = current_stock.get("FlipLog", "")
        currently_alive_vials = current_stock.get("CurrentlyAliveVials", "")

        if "," in flip_log:
            last_vial = int(flip_log.split(",")[0].strip()[1:])
            last_vial += 1
            flip_log = f"V{last_vial}, {ts}; {flip_log}"
            currently_alive_vials = f"{currently_alive_vials}, V{last_vial}"
        else:
            flip_log = f"V1, {ts}"
            currently_alive_vials = "V1"

        update_fields["FlipLog"] = flip_log
        update_fields["CurrentlyAliveVials"] = currently_alive_vials

        # Prepare the modification log
        modification_log_entries = []

        # If new status is provided and different from current, update it
        if new_status and current_stock.get("Status") != new_status:
            update_fields["Status"] = new_status
            update_fields["DataModifiedDate"] = ts
            modification_log_entries.append(
                f"{ts} : Status changed from {current_stock.get('Status')} to {new_status}"
            )

        # If comment is provided, add it to the comments and the modification log
        if added_comment:
            comments = current_stock.get("Comments", "")
            update_fields["Comments"] = (
                f"{added_comment}; {comments}" if comments else added_comment
            )
            modification_log_entries.append(f"{ts} : Comments added: {added_comment}")

        # If there are modification log entries, concatenate them with the existing log
        if modification_log_entries:
            modification_log = current_stock.get("ModificationLog", "")
            new_modification_log = "; ".join(modification_log_entries)
            update_fields["ModificationLog"] = (
                f"{new_modification_log}; {modification_log}"
                if modification_log
                else new_modification_log
            )

        # Update the stock document in MongoDB
        stocks_collection.update_one(
            {"UniqueID": uid, "User": user}, {"$set": update_fields}
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
                modification_log = current_stock.get("ModificationLog", "")
                new_modification_log = "; ".join(modification_log_entries)
                update_fields["ModificationLog"] = (
                    f"{new_modification_log}; {modification_log}"
                    if modification_log
                    else new_modification_log
                )
                update_fields["DataModifiedDate"] = timestamp

            # Update the stock document in MongoDB
            result = stocks_collection.update_one(
                {"UniqueID": uid, "User": user}, {"$set": update_fields}
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

    try:
        for prop in REQUIRED_STOCK_PROPERTIES:
            if prop not in stock:
                raise ValueError(f"{prop} is required")
    except ValueError as e:
        # fill in the missing properties with default values
        update_properties = {}
        for prop in REQUIRED_STOCK_PROPERTIES:
            if prop not in stock:
                if prop in DEFAULT_STOCK_PROPERTY_VALUES:
                    update_properties[prop] = DEFAULT_STOCK_PROPERTY_VALUES[prop]
                else:
                    raise ValueError(f"{prop} is required")
        # edit the stock
        success = edit_stock(username, uid, db, update_properties, log_activity=False)
        if not success:
            print(f"Failed to update stock {uid} with default values")
            return False

    # check if the stock doesnt have the key "CurrentlyAliveVials"
    if "CurrentlyAliveVials" not in stock:
        assert all(
            [
                x in stock
                for x in [
                    "FlipLog",
                    "VialLifetime",
                    "FlipFrequency",
                    "DevelopmentalTime",
                ]
            ]
        ), "Stock must have the keys FlipLog, VialLifetime, and FlipFrequency"

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

        vials = ["V{}".format(i) for i in range(1, len(flip_dates) + 1)]
        next_flip_dates = [
            date + datetime.timedelta(days=flipFrequency) for date in flip_dates
        ]
        next_eclosion_dates = [
            date + datetime.timedelta(days=developmentalTime) for date in flip_dates
        ]

        # recreate a new flip log
        flipLog = [
            f"{vial}, {date.strftime('%Y-%m-%d %H:%M')}"
            for vial, date in zip(vials[::-1], flip_dates[::-1])
        ]
        flipLog = "; ".join(flipLog)

        # remove HH:MM:SS from the dates
        flip_dates = [
            date.replace(hour=0, minute=0, second=0, microsecond=0)
            for date in flip_dates
        ]
        next_flip_dates = [
            date.replace(hour=0, minute=0, second=0, microsecond=0)
            for date in next_flip_dates
        ]
        next_eclosion_dates = [
            date.replace(hour=0, minute=0, second=0, microsecond=0)
            for date in next_eclosion_dates
        ]

        # determine the dead vials
        currently_alive_vials = vials.copy()
        to_delete = []
        for vial, flip_date, next_flip_date in zip(vials, flip_dates, next_flip_dates):
            death_date = flip_date + datetime.timedelta(days=vialLifetime)
            # if death date has passed and there is atleast one flip AFTER the scheduled death date
            if datetime.datetime.now() > death_date and any(
                [date >= next_flip_date for date in flip_dates]
            ):
                # get the index of the vial
                index = currently_alive_vials.index(vial)
                to_delete.append(index)
                print(f"Vial {vial} for stock {uid} has died")

        # remove the dead vials
        currently_alive_vials = [
            vial for i, vial in enumerate(currently_alive_vials) if i not in to_delete
        ]
        next_flip_dates = [
            date for i, date in enumerate(next_flip_dates) if i not in to_delete
        ]
        next_eclosion_dates = [
            date for i, date in enumerate(next_eclosion_dates) if i not in to_delete
        ]

        # get the last flip date
        try:
            last_flip_date = datetime.datetime.strptime(
                stock["LastFlipDate"], "%Y-%m-%d %H:%M"
            )
        except:
            last_flip_date = datetime.datetime.strptime(
                stock["LastFlipDate"], "%Y-%m-%d %H:%M:%S"
            )
        # remove HH:MM:SS from the last flip date
        last_flip_date = last_flip_date.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # remove all next flip dates that are before the last flip date + 1 day to account for early flips
        next_flip_dates = [
            date
            for date in next_flip_dates
            if date
            > last_flip_date + datetime.timedelta(days=math.floor(flipFrequency // 2))
        ]

        # define the currently alive vials
        currently_alive_vials = ", ".join(currently_alive_vials)
        # define the next flip dates
        next_flip_dates = ", ".join(
            [date.strftime("%Y-%m-%d") for date in next_flip_dates]
        )
        # define the next eclosion dates
        next_eclosion_dates = ", ".join(
            [date.strftime("%Y-%m-%d") for date in next_eclosion_dates]
        )

        # update the stock
        update_properties = {
            "FlipLog": flipLog,
            "CurrentlyAliveVials": currently_alive_vials,
            "NextFlipDates": next_flip_dates,
            "NextEclosionDates": next_eclosion_dates,
        }

        # check if currently alive vials is empty
        if currently_alive_vials == "":
            update_properties["Status"] = "No longer maintained"

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
        next_flip_dates = [
            date + datetime.timedelta(days=flipFrequency) for date in dates
        ]
        # get the next eclosion dates
        developmentalTime = float(stock["DevelopmentalTime"])
        next_eclosion_dates = [
            date + datetime.timedelta(days=developmentalTime) for date in dates
        ]

        # remove HH:MM:SS from the dates
        flip_dates = [
            date.replace(hour=0, minute=0, second=0, microsecond=0)
            for date in flip_dates
        ]
        next_flip_dates = [
            date.replace(hour=0, minute=0, second=0, microsecond=0)
            for date in next_flip_dates
        ]
        next_eclosion_dates = [
            date.replace(hour=0, minute=0, second=0, microsecond=0)
            for date in next_eclosion_dates
        ]

        # determine the dead vials
        vialLifetime = float(stock["VialLifetime"])
        to_delete = []
        for vial, flip_date, next_flip_date in zip(
            currently_alive_vials, dates, next_flip_dates
        ):
            death_date = flip_date + datetime.timedelta(days=vialLifetime)
            # if death date has passed and there is atleast one flip AFTER the scheduled death date
            if datetime.datetime.now() > death_date and any(
                [date >= next_flip_date for date in flip_dates]
            ):
                # get the index of the vial
                index = currently_alive_vials.index(vial)
                to_delete.append(index)
                print(f"Vial {vial} for stock {uid} has died")

        # remove the dead vials
        currently_alive_vials = [
            vial for i, vial in enumerate(currently_alive_vials) if i not in to_delete
        ]
        next_flip_dates = [
            date for i, date in enumerate(next_flip_dates) if i not in to_delete
        ]
        next_eclosion_dates = [
            date for i, date in enumerate(next_eclosion_dates) if i not in to_delete
        ]

        # get the last flip date
        try:
            last_flip_date = datetime.datetime.strptime(
                stock["LastFlipDate"], "%Y-%m-%d %H:%M"
            )
        except:
            last_flip_date = datetime.datetime.strptime(
                stock["LastFlipDate"], "%Y-%m-%d %H:%M:%S"
            )
        # remove HH:MM:SS from the last flip date
        last_flip_date = last_flip_date.replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # remove all next flip dates that are before the last flip date + 1 day to account for early flips
        next_flip_dates = [
            date
            for date in next_flip_dates
            if date
            > last_flip_date + datetime.timedelta(days=math.floor(flipFrequency // 2))
        ]

        # define the currently alive vials
        currently_alive_vials = ", ".join(currently_alive_vials)
        # define the next flip dates
        next_flip_dates = ", ".join(
            [date.strftime("%Y-%m-%d") for date in next_flip_dates]
        )
        # define the next eclosion dates
        next_eclosion_dates = ", ".join(
            [date.strftime("%Y-%m-%d") for date in next_eclosion_dates]
        )
        # update the stock
        update_properties = {
            "CurrentlyAliveVials": currently_alive_vials,
            "NextFlipDates": next_flip_dates,
            "NextEclosionDates": next_eclosion_dates,
        }

        # check if currently alive vials is empty
        if currently_alive_vials == "":
            update_properties["Status"] = "No longer maintained"

        # edit the stock
        success = edit_stock(
            username,
            uid,
            db,
            update_properties,
            log_activity=False,
            refresh_vials=False,
        )
    return success
