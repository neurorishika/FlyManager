import datetime
import math
from hashlib import shake_256
from flymanager.utils.genetics import qc_genotype
from flymanager.utils.utils import clean_log_entry
from flymanager.app.settings import BASE_CROSS_PROPERTIES, REQUIRED_CROSS_PROPERTIES, OPTIONAL_CROSS_PROPERTIES, DEFAULT_CROSS_PROPERTY_VALUES

def add_to_cross(user, properties, db):
    """
    Add a cross to the user's cross collection in MongoDB.
    
    Parameters:
    user: str
        The username of the user.
    properties: dict
        The properties of the cross.
        Properties:
            SEE REQUIRED_CROSS_PROPERTIES and OPTIONAL_CROSS_PROPERTIES 
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the cross was added, False otherwise.
    uid: str
        The unique identifier of the cross.
    """

    # Check for required fields
    for prop in REQUIRED_CROSS_PROPERTIES + BASE_CROSS_PROPERTIES:
        if prop not in properties:
            raise ValueError(f"{prop} is required")

    # Create a UniqueID for the cross based on Male and Female UniqueID + User + Name
    uid = str(user) + str(properties["MaleUniqueID"]) + str(properties["FemaleUniqueID"]) + str(properties["Name"])
    
    # Import here to avoid circular imports
    from .db import uid_exists
    
    # Hash the UID
    uid = shake_256(uid.encode()).hexdigest(5)

    # Ensure the UniqueID is unique
    while uid_exists(uid, db):
        print("UniqueID already exists, generating a new one")
        uid = shake_256(uid.encode()).hexdigest(5)

    # Get the current timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # make sure genotype meets the qc
    qc, male_genotype = qc_genotype(properties["MaleGenotype"])
    if not qc:
        return False, male_genotype

    qc, female_genotype = qc_genotype(properties["FemaleGenotype"])
    if not qc:
        return False, female_genotype

    # Create the document to insert
    cross_document = {
        "UniqueID": uid,
        "User": user,
        "MaleUniqueID": properties["MaleUniqueID"],
        "FemaleUniqueID": properties["FemaleUniqueID"],
        "MaleGenotype": male_genotype,
        "FemaleGenotype": female_genotype,
        "Name": properties["Name"],
        "TrayID": "",
        "TrayPosition": "",
        "CreationDate": timestamp,
        "DataModifiedDate": timestamp,
        "ModificationLog": f"{timestamp} : Cross created",
        "LastFlipDate": timestamp,
        "FlipLog": timestamp,
    }

    for prop in REQUIRED_CROSS_PROPERTIES:
        cross_document[prop] = properties[prop]

    for prop in OPTIONAL_CROSS_PROPERTIES:
        cross_document[prop] = properties.get(prop, "")

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

    try:
        for prop in REQUIRED_CROSS_PROPERTIES:
            if prop not in cross:
                raise ValueError(f"{prop} is required")
    except ValueError as e:
        # fill in the missing properties with default values
        update_properties = {}
        for prop in REQUIRED_CROSS_PROPERTIES:
            if prop not in cross: 
                if prop in DEFAULT_CROSS_PROPERTY_VALUES:
                    update_properties[prop] = DEFAULT_CROSS_PROPERTY_VALUES[prop]
                else:
                    raise ValueError(f"{prop} is required")
        # edit the cross
        success = edit_cross(username, uid, db, update_properties, log_activity=False)
        if not success:
            print(f"Failed to update cross {uid} with default values")
            return False
    
    # check if the cross doesnt have the key "CurrentlyAliveVials"
    if "CurrentlyAliveVials" not in cross:
        
        # get the cross details
        flipLog = cross["FlipLog"]
        vialLifetime = float(cross["VialLifetime"])
        flipFrequency = float(cross["FlipFrequency"])
        developmentalTime = float(cross["DevelopmentalTime"])
        maxCrossLifetime = float(cross["MaxCrossLifetime"])

        # split the flip log into a list by ";"
        flipLog = flipLog.split(";")
        _, flip_dates = zip(*[clean_log_entry(flip) for flip in flipLog])

        # reverse the order of the vials and dates
        flip_dates = flip_dates[::-1]

        # get the first flip date
        first_flip_date = flip_dates[0]
            
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
        next_flip_dates = [date for date in next_flip_dates if date > last_flip_date + datetime.timedelta(days=math.floor(flipFrequency//2))]
        
        # remove all next flip dates that are after the max cross lifetime from the first flip date
        next_flip_dates = [date for date in next_flip_dates if date <= first_flip_date + datetime.timedelta(days=maxCrossLifetime)]

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

        # check if currently alive vials is empty
        if currently_alive_vials == "":
            update_properties["Status"] = "No longer maintained"

        # edit the cross
        success = edit_cross(username, uid, db, update_properties, log_activity=False)
        
    else:
        # get all flip dates
        flipLog = cross["FlipLog"]
        flipLog = flipLog.split(";")

        # get the first flip date
        first_flip_date = clean_log_entry(flipLog[-1])[1]

        # create a dictionary of vials and their flip dates
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
        # get the max cross lifetime
        maxCrossLifetime = float(cross["MaxCrossLifetime"])
        
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
        next_flip_dates = [date for date in next_flip_dates if date > last_flip_date + datetime.timedelta(days=math.floor(flipFrequency//2))]
        
        # remove all next flip dates that are after the max cross lifetime from the first flip date
        next_flip_dates = [date for date in next_flip_dates if date <= first_flip_date + datetime.timedelta(days=maxCrossLifetime)]

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

        # check if currently alive vials is empty
        if currently_alive_vials == "":
            update_properties["Status"] = "No longer maintained"
            
        # edit the cross
        success = edit_cross(username, uid, db, update_properties, log_activity=False, refresh_vials=False)
    return success