import datetime
from hashlib import shake_256


def add_tray(user, properties, db):
    """
    Add a tray to the user's tray collection in MongoDB.

    Parameters:
    user: str
        The username of the user.
    properties: dict
        The properties of the tray.
        Properties:
            TrayID (required): A unique identifier for the tray
            Name (required): A descriptive name for the tray
            Rows (required): Number of rows in the tray
            Columns (required): Number of columns in the tray
            Description (optional): Additional details about the tray
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the tray was added, False otherwise.
    """
    # Import locally to avoid circular imports
    from flymanager.utils.mongo.db import uid_exists

    # Validate required fields
    assert "TrayID" in properties, "TrayID is required"
    assert "Name" in properties, "Name is required"
    assert "Rows" in properties, "Rows is required"
    assert "Columns" in properties, "Columns is required"

    # Create a unique ID for the tray
    uid = f"{user}_{properties['TrayID']}"
    uid = shake_256(uid.encode()).hexdigest(5)

    # Ensure the TrayID is unique for this user
    trays_collection = db["trays"]
    existing_tray = trays_collection.find_one(
        {"User": user, "TrayID": properties["TrayID"]}
    )
    if existing_tray:
        return False, "TrayID already exists for this user"

    # Get current timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Create the document
    tray_document = {
        "UniqueID": uid,
        "User": user,
        "TrayID": properties["TrayID"],
        "Name": properties["Name"],
        "Rows": int(properties["Rows"]),
        "Columns": int(properties["Columns"]),
        "Description": properties.get("Description", ""),
        "CreationDate": timestamp,
        "DataModifiedDate": timestamp,
        "ModificationLog": f"{timestamp} : Tray created",
    }

    # Insert into database
    trays_collection.insert_one(tray_document)

    return True, uid


def get_user_trays(user, db):
    """
    Get all trays for a user from MongoDB.

    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    list
        A list of all trays for the user.
    """
    trays_collection = db["trays"]
    trays = trays_collection.find({"User": user})
    return list(trays)


def get_tray(user, tray_id, db):
    """
    Get a specific tray by ID from MongoDB.

    Parameters:
    user: str
        The username of the user.
    tray_id: str
        The TrayID of the tray.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    dict
        The tray document, or None if not found.
    """
    trays_collection = db["trays"]
    tray = trays_collection.find_one({"User": user, "TrayID": tray_id})
    return tray


def delete_tray(user, tray_id, db):
    """
    Delete a tray from MongoDB.

    Parameters:
    user: str
        The username of the user.
    tray_id: str
        The TrayID of the tray to delete.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the tray was deleted, False otherwise.
    """
    trays_collection = db["trays"]
    result = trays_collection.delete_one({"User": user, "TrayID": tray_id})
    return result.deleted_count > 0


def update_tray(user, tray_id, updates, db):
    """
    Update a tray in MongoDB.

    Parameters:
    user: str
        The username of the user.
    tray_id: str
        The TrayID of the tray to update.
    updates: dict
        A dictionary of fields to update and their new values.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the tray was updated, False otherwise.
    """
    trays_collection = db["trays"]

    # Prepare the update fields
    update_fields = {}
    modification_log_entries = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Add each update to the update fields and log
    for field, value in updates.items():
        # TrayID cannot be changed
        if field == "TrayID":
            continue

        update_fields[field] = value
        modification_log_entries.append(f"{timestamp} : {field} updated to {value}")

    # If there are updates, add to the ModificationLog and DataModifiedDate
    if modification_log_entries:
        current_tray = trays_collection.find_one({"User": user, "TrayID": tray_id})
        if current_tray:
            modification_log = current_tray.get("ModificationLog", "")
            new_modification_log = "; ".join(modification_log_entries)
            update_fields["ModificationLog"] = (
                f"{new_modification_log}; {modification_log}"
                if modification_log
                else new_modification_log
            )
            update_fields["DataModifiedDate"] = timestamp

            # Update the tray document in MongoDB
            result = trays_collection.update_one(
                {"User": user, "TrayID": tray_id}, {"$set": update_fields}
            )

            return result.matched_count > 0

    return False


def get_tray_occupancy(user, tray_id, db):
    """
    Get the current occupancy of a tray.

    Parameters:
    user: str
        The username of the user.
    tray_id: str
        The TrayID of the tray.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    dict
        Dictionary of occupied and blocked positions with stock/cross info
    """
    # Get stocks and crosses in this tray
    stocks_collection = db["stocks"]
    crosses_collection = db["crosses"]

    stocks = stocks_collection.find({"User": user, "TrayID": tray_id})
    crosses = crosses_collection.find({"User": user, "TrayID": tray_id})

    # Create occupancy map
    occupancy = {}

    # Add stocks to occupancy map
    for stock in stocks:
        position = stock.get("TrayPosition", "")
        if position and stock["Status"] != "No longer maintained":
            # Calculate required vials for blocking
            required_vials = calculate_required_vials(stock)
            position_int = int(position)

            # Calculate row and column for horizontal blocking
            row = ((position_int - 1) % 10) + 1
            column = ((position_int - 1) // 10) + 1

            # Add the main position
            occupancy[position] = {
                "type": "stock",
                "id": stock["UniqueID"],
                "name": stock["Name"],
                "genotype": stock["Genotype"],
                "status": stock["Status"],
                "required_vials": required_vials,
                "display_name": f"{stock['Name']} ({stock['UniqueID']})",
            }

            # Block the next positions horizontally in the same row
            for i in range(1, required_vials):
                # Calculate next position in the row
                next_column = column + i
                blocked_pos = str(((next_column - 1) * 10) + row)
                occupancy[blocked_pos] = {
                    "type": "blocked",
                    "blocked_by": position,
                    "blocked_by_type": "stock",
                    "blocked_by_name": f"{stock['Name']} ({stock['UniqueID']})",
                }

    # Add crosses to occupancy map
    for cross in crosses:
        position = cross.get("TrayPosition", "")
        if position and cross["Status"] != "No longer maintained":
            # Calculate required vials for blocking
            required_vials = calculate_required_vials(cross)
            position_int = int(position)

            # Calculate row and column for horizontal blocking
            row = ((position_int - 1) % 10) + 1
            column = ((position_int - 1) // 10) + 1

            # Get the stock IDs for male and female
            male_stock = stocks_collection.find_one(
                {"User": user, "UniqueID": cross["MaleUniqueID"]}
            )
            female_stock = stocks_collection.find_one(
                {"User": user, "UniqueID": cross["FemaleUniqueID"]}
            )

            male_id = male_stock["UniqueID"] if male_stock else "Unknown"
            female_id = female_stock["UniqueID"] if female_stock else "Unknown"

            # Add the main position
            occupancy[position] = {
                "type": "cross",
                "id": cross["UniqueID"],
                "name": cross["Name"],
                "male_genotype": cross["MaleGenotype"],
                "female_genotype": cross["FemaleGenotype"],
                "status": cross["Status"],
                "required_vials": required_vials,
                "male_stock_id": male_id,
                "female_stock_id": female_id,
                "display_name": f"{cross['Name']} ({cross['UniqueID']}, ♂:{male_id}, ♀:{female_id})",
            }

            # Block the next positions horizontally in the same row
            for i in range(1, required_vials):
                # Calculate next position in the row
                next_column = column + i
                blocked_pos = str(((next_column - 1) * 10) + row)
                occupancy[blocked_pos] = {
                    "type": "blocked",
                    "blocked_by": position,
                    "blocked_by_type": "cross",
                    "blocked_by_name": f"{cross['Name']} ({cross['UniqueID']})",
                }

    return occupancy


def calculate_required_vials(stock_or_cross):
    """
    Calculate the number of vials required for a stock or cross based on
    its vial lifetime and flip frequency.

    Parameters:
    stock_or_cross: dict
        The stock or cross document from MongoDB.

    Returns:
    int
        The number of vials required.
    """
    vial_lifetime = float(stock_or_cross["VialLifetime"])
    flip_frequency = float(stock_or_cross["FlipFrequency"])

    # Calculate how many vials will be alive at any given time
    required_vials = int((vial_lifetime / flip_frequency) + 0.99)  # Round up
    return required_vials


def move_item_to_tray(user, item_type, item_id, tray_id, position, db):
    """
    Move a stock or cross to a specific tray and position.

    Parameters:
    user: str
        The username of the user.
    item_type: str
        Either "stock" or "cross"
    item_id: str
        The UniqueID of the stock or cross.
    tray_id: str
        The TrayID to move to, or empty string to remove from tray.
    position: str
        The position in the tray (e.g. "1", "2", etc.), or empty string to remove from tray.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    bool
        True if the item was moved/removed, False otherwise.
    """
    # Import to avoid circular imports
    from flymanager.utils.mongo.stocks import edit_stock, get_stock
    from flymanager.utils.mongo.crosses import edit_cross, get_cross

    # Handle removal from tray (empty tray_id and position)
    if tray_id == "" and position == "":
        updates = {"TrayID": "", "TrayPosition": ""}
        if item_type == "stock":
            return edit_stock(user, item_id, db, updates)
        elif item_type == "cross":
            return edit_cross(user, item_id, db, updates)
        else:
            return False

    # Validate the tray exists for moves to a tray
    tray = get_tray(user, tray_id, db)
    if not tray:
        return False

    # Get the item to calculate required vials
    if item_type == "stock":
        item = get_stock(user, item_id, db)
    elif item_type == "cross":
        item = get_cross(user, item_id, db)
    else:
        return False

    if not item or item.get("Status") == "No longer maintained":
        return False

    # Calculate required vials
    required_vials = calculate_required_vials(item)

    # Check if position is within tray bounds
    try:
        position_int = int(position)
        if position_int <= 0 or position_int > (tray["Rows"] * tray["Columns"]):
            return False

        # Calculate row and column for horizontal validation
        row = ((position_int - 1) % 10) + 1
        column = ((position_int - 1) // 10) + 1

        # Check if the last required position would be beyond tray bounds horizontally
        last_column = column + required_vials - 1
        if last_column > tray["Columns"]:
            return False
    except ValueError:
        return False

    # Check if any of the required positions are occupied
    occupancy = get_tray_occupancy(user, tray_id, db)
    for i in range(required_vials):
        # Calculate next position in the row
        next_column = column + i
        check_pos = str(((next_column - 1) * 10) + row)
        if check_pos in occupancy:
            return False

    # Move the item
    updates = {"TrayID": tray_id, "TrayPosition": position}

    if item_type == "stock":
        return edit_stock(user, item_id, db, updates)
    elif item_type == "cross":
        return edit_cross(user, item_id, db, updates)
    else:
        return False
