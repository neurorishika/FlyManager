import datetime
from threading import RLock

from flask import url_for

from flymanager.utils.utils import day_str_to_num

_METADATA_TYPES = (
    "types",
    "food_types",
    "provenances",
    "genesX",
    "genes2nd",
    "genes3rd",
    "genes4th",
    "species",
)
_METADATA_CACHE = {}
_METADATA_CACHE_LOCK = RLock()


def _metadata_cache_key(metadata_type, db):
    return str(getattr(db, "name", "")), metadata_type


def _invalidate_metadata_cache(metadata_type, db):
    with _METADATA_CACHE_LOCK:
        _METADATA_CACHE.pop(_metadata_cache_key(metadata_type, db), None)


def clear_metadata_cache():
    with _METADATA_CACHE_LOCK:
        _METADATA_CACHE.clear()


def preload_metadata_cache(db, metadata_types=None):
    selected_types = tuple(metadata_types or _METADATA_TYPES)
    return {
        metadata_type: get_metadata(metadata_type, db)
        for metadata_type in selected_types
    }

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
    assert metadata_type in _METADATA_TYPES, "Invalid metadata type"
    cache_key = _metadata_cache_key(metadata_type, db)

    with _METADATA_CACHE_LOCK:
        cached_values = _METADATA_CACHE.get(cache_key)
    if cached_values is not None:
        return list(cached_values)

    metadata_collection = db[metadata_type]
    metadata = list(metadata_collection.find())
    values = tuple(m["Value"] for m in metadata)

    with _METADATA_CACHE_LOCK:
        _METADATA_CACHE[cache_key] = values

    return list(values)


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
    assert metadata_type in _METADATA_TYPES, "Invalid metadata type"

    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Check if the metadata already exists
    if metadata_collection.find_one({"Value": metadata_value}):
        return False

    # Add the metadata document to the collection
    metadata_document = {"Value": metadata_value}

    metadata_collection.insert_one(metadata_document)
    _invalidate_metadata_cache(metadata_type, db)

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
    assert metadata_type in _METADATA_TYPES, "Invalid metadata type"

    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Attempt to delete the metadata
    result = metadata_collection.delete_one({"Value": metadata_value})

    # Check if any document was deleted
    if result.deleted_count > 0:
        _invalidate_metadata_cache(metadata_type, db)
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
    assert metadata_type in _METADATA_TYPES, "Invalid metadata type"

    # Define the metadata collection
    metadata_collection = db[metadata_type]

    # Attempt to update the metadata
    result = metadata_collection.update_one(
        {"Value": old_value}, {"$set": {"Value": new_value}}
    )

    if result.matched_count > 0:
        _invalidate_metadata_cache(metadata_type, db)
        return True

    return False


# Flip Schedule Utilities


def get_flip_schedule(user, db):
    """
    Get a date-by-date schedule for which stocks and crosses need to be flipped, including tray info and links.

    Parameters:
    user (str): The username of the current user.
    db (MongoClient): The database instance.

    Returns:
    dict: A dictionary where the keys are dates and the values are lists of stocks/crosses to flip on those dates.
    """
    # Import locally to avoid circular imports
    from flymanager.utils.mongo.access import (get_maintainable_crosses,
                                               get_maintainable_stocks)
    from flymanager.utils.mongo.user_data import get_user_flip_days

    # Retrieve user's stocks and crosses
    stocks = get_maintainable_stocks(user, db)
    crosses = get_maintainable_crosses(user, db)

    # Remove ones with Status = "No longer maintained"
    stocks = [stock for stock in stocks if stock["Status"] != "No longer maintained"]
    crosses = [cross for cross in crosses if cross["Status"] != "No longer maintained"]

    # Sort stocks and crosses by TrayID and TrayPosition
    stocks = sorted(
        stocks,
        key=lambda x: (
            x["TrayID"],
            int(float(x["TrayPosition"])) if x["TrayPosition"] != "" else 0,
        ),
    )
    crosses = sorted(
        crosses,
        key=lambda x: (
            x["TrayID"],
            int(float(x["TrayPosition"])) if x["TrayPosition"] != "" else 0,
        ),
    )

    # Get user's preferred flip days
    flip_days = get_user_flip_days(user, db)

    # Initialize a schedule dictionary where each key is a date and value is a list of stocks/crosses
    schedule = {}

    # Process stocks
    for stock in stocks:
        next_flip_dates = stock["NextFlipDates"].split(", ")
        for next_flip_date in next_flip_dates:
            closest_flip_day = find_closest_flip_day(next_flip_date, flip_days)
            if closest_flip_day:
                flip_date_str = closest_flip_day.strftime("%Y-%m-%d")
                tray_info = f"{stock['TrayID']} - {stock['TrayPosition']}"
                owner_note = f", owner: {stock['User']}" if stock.get("User") != user else ""
                try:
                    # Try to generate URL - this will fail outside of request context
                    link = f"<a href='{url_for('stock.view_stock', unique_id=stock['UniqueID'])}'>{stock['Name']}</a>"
                except RuntimeError:
                    # Fallback for scheduled tasks - just show the name without link
                    link = stock["Name"]
                schedule.setdefault(flip_date_str, []).append(
                    f"Stock: {link} (ID: {stock['UniqueID']}, {tray_info}{owner_note})"
                )
            else:
                print(
                    f"No valid flip day found for stock {stock['UniqueID']} on {next_flip_date}"
                )

    # Process crosses
    for cross in crosses:
        next_flip_dates = cross["NextFlipDates"].split(", ")
        for next_flip_date in next_flip_dates:
            closest_flip_day = find_closest_flip_day(next_flip_date, flip_days)
            if closest_flip_day:
                flip_date_str = closest_flip_day.strftime("%Y-%m-%d")
                tray_info = f"{cross['TrayID']} - {cross['TrayPosition']}"
                owner_note = f", owner: {cross['User']}" if cross.get("User") != user else ""
                try:
                    # Try to generate URL - this will fail outside of request context
                    uid_url = url_for("cross.view_cross", unique_id=cross["UniqueID"])
                    link = f"<a href='{uid_url}'>{cross['Name']}</a>"
                except RuntimeError:
                    # Fallback for scheduled tasks - just show the name without link
                    link = cross["Name"]
                schedule.setdefault(flip_date_str, []).append(
                    f"Cross: {link} (ID: {cross['UniqueID']}, {tray_info}{owner_note})"
                )

    # Sort the schedule by date
    sorted_schedule = dict(sorted(schedule.items()))

    return sorted_schedule


def get_flip_in(item):
    """
    Get the flip in for a stock or cross.

    Parameters:
    item: dict
        The stock or cross document.

    Returns:
    str
        A string representing the days until the next flip.
    """
    vals = []
    for val in item["NextFlipDates"].split(", "):
        try:
            next_time = datetime.datetime.strptime(val, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            now = datetime.datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            vals.append(round((next_time - now).days))
        except:
            vals.append("N/A")
    return ", ".join([str(val) for val in vals]) + " days"


def get_eclosion_in(item):
    """
    Get the eclosion in for a stock or cross.

    Parameters:
    item: dict
        The stock or cross document.

    Returns:
    str
        A string representing the days until the next eclosion.
    """
    vals = []
    for val in item["NextEclosionDates"].split(", "):
        try:
            next_time = datetime.datetime.strptime(val, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            now = datetime.datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            vals.append(round((next_time - now).days))
        except:
            vals.append("N/A")
    return ", ".join([str(val) for val in vals]) + " days"


def find_closest_flip_day(next_flip_date, flip_days, debug=False):
    """
    Find the closest preferred flip day to the next flip date.

    Parameters:
    next_flip_date (datetime or str): The next flip date.
    flip_days (list): List of user's preferred flip days in string format like ["Mo", "We", "Fr"].
    debug (bool): Whether to print debug information.

    Returns:
    datetime: The closest flip date based on user's preferred days or None if no valid day is found.
    """

    # Convert preferred flip days from string to day numbers (0 = Monday, ..., 6 = Sunday)
    preferred_days = [day_str_to_num(day) for day in flip_days]

    # if next_flip_date is empty, return None
    if not next_flip_date:
        return None

    # Convert next flip date to datetime if it's a string
    next_flip_date = datetime.datetime.strptime(next_flip_date, "%Y-%m-%d")

    assert isinstance(
        next_flip_date, datetime.datetime
    ), "next_flip_date must be a datetime object"

    # Get the day of the week for the next flip date (0 = Monday, ..., 6 = Sunday)
    next_flip_day = next_flip_date.weekday()

    # if the current day is a preferred flip day, return the next flip date
    if next_flip_day in preferred_days:
        return next_flip_date

    # order the preferred days starting from the next flip day
    preferred_days = sorted(preferred_days, key=lambda x: (x - next_flip_day) % 7)

    if debug:
        print(
            f"Next flip date: {next_flip_date}, Next flip day: {next_flip_day}, Preferred days: {preferred_days}"
        )

    # Initialize variables for the closest day
    closest_day = None
    min_diff = float("inf")

    # Check preferred flip days to find the closest valid one
    for preferred_day in preferred_days:
        # Calculate the difference in days (can only be one day before or up to two days after)
        diff_forward = (
            preferred_day - next_flip_day + 7
        ) % 7  # Days after the next flip date
        diff_backward = (
            next_flip_day - preferred_day + 7
        ) % 7  # Days before the next flip date

        if debug:
            print(
                f"Preferred day: {preferred_day}, Forward diff: {diff_forward}, Backward diff: {diff_backward}"
            )

        if diff_forward <= 2:  # Check for days up to two days after
            if diff_forward < min_diff:
                min_diff = diff_forward
                closest_day = preferred_day

        if diff_backward == 1:  # Check for exactly one day before
            if diff_backward < min_diff:
                min_diff = diff_backward
                closest_day = preferred_day

    # If a closest day is found, calculate the date for that day
    if closest_day is not None:
        if min_diff <= 2:
            days_ahead = (closest_day - next_flip_day + 7) % 7
            closest_flip_date = next_flip_date + datetime.timedelta(days=days_ahead)
            return closest_flip_date

    # If no valid day is found, return the original next flip date
    return next_flip_date
