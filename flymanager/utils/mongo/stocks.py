from flymanager.app.settings import (BASE_STOCK_PROPERTIES,
                                     DEFAULT_STOCK_PROPERTY_VALUES,
                                     OPTIONAL_STOCK_PROPERTIES,
                                     REQUIRED_STOCK_PROPERTIES)
from flymanager.utils.genetics import qc_genotype
from flymanager.utils.mongo_records import (apply_updates_to_owned_document,
                                            build_existing_vial_timeline,
                                            build_initial_vial_timeline,
                                            build_owned_document,
                                            build_vial_update_properties,
                                            current_timestamp,
                                            delete_owned_document,
                                            flip_owned_document,
                                            generate_unique_id,
                                            get_missing_required_updates,
                                            get_owned_document,
                                            prune_vial_schedule,
                                            require_fields)
from flymanager.utils.phenotypes.predictor import build_stock_phenotype_cache


def build_stock_standardization_cache(genotype):
    # Imported lazily: the standardization service lives under
    # ``flymanager.app.services`` and importing it at module load would pull the
    # app package in during MongoDB bootstrap, creating a circular import.
    from flymanager.app.services.stock_standardization import \
        build_stock_standardization_cache as _build
    return _build(genotype)


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

    require_fields(properties, REQUIRED_STOCK_PROPERTIES + BASE_STOCK_PROPERTIES)

    # make sure genotype meets the qc
    qc, genotype = qc_genotype(properties["Genotype"])

    if not qc:
        return False, genotype

    # create UniqueID as a hash of the (User + Genotype + SeriesID + ReplicateID)
    uid = generate_unique_id(
        [
            user,
            properties["Genotype"],
            properties.get("StockSource", DEFAULT_STOCK_PROPERTY_VALUES["StockSource"]),
            properties["SeriesID"],
            properties["ReplicateID"],
        ],
        db,
    )

    # get creation timestamp
    timestamp = current_timestamp()

    stock_document = build_owned_document(
        user=user,
        uid=uid,
        properties=properties,
        required_properties=REQUIRED_STOCK_PROPERTIES,
        optional_properties=OPTIONAL_STOCK_PROPERTIES,
        base_document={
            "Genotype": genotype,
            "PhenotypeCache": build_stock_phenotype_cache(genotype),
            "StandardizationCache": build_stock_standardization_cache(genotype),
            "Name": properties["Name"],
            "TrayID": "",
            "TrayPosition": "",
            "CreationDate": timestamp,
            "LastFlipDate": timestamp,
            "FlipLog": timestamp,
            "DataModifiedDate": timestamp,
            "ModificationLog": f"{timestamp} : Stock created",
        },
    )

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

    return get_owned_document("stocks", user, uid, db, admin_include=admin_include)


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

    print(f"Flipping stock {uid} for user {user}")
    updated_stock = flip_owned_document(
        "stocks",
        user,
        uid,
        db,
        timestamp,
        new_status=new_status,
        added_comment=added_comment,
        accept_datetime=True,
    )
    if updated_stock:
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

    return delete_owned_document("stocks", user, uid, db)


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

    prepared_updates = dict(updates)
    if "Genotype" in prepared_updates:
        prepared_updates["PhenotypeCache"] = build_stock_phenotype_cache(
            prepared_updates["Genotype"]
        )
        prepared_updates["StandardizationCache"] = build_stock_standardization_cache(
            prepared_updates["Genotype"]
        )

    success, current_stock = apply_updates_to_owned_document(
        "stocks",
        user,
        uid,
        db,
        prepared_updates,
        log_activity=log_activity,
    )

    if success and "Genotype" in prepared_updates:
        from flymanager.utils.mongo.crosses import \
            propagate_stock_genotype_to_crosses
        propagate_stock_genotype_to_crosses(user, uid, prepared_updates["Genotype"], db)

    if success and refresh_vials and current_stock:
        update_stock_vials(current_stock, user, db)

    return success


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

    update_properties = get_missing_required_updates(
        stock,
        REQUIRED_STOCK_PROPERTIES,
        DEFAULT_STOCK_PROPERTY_VALUES,
    )
    if update_properties:
        success = edit_stock(username, uid, db, update_properties, log_activity=False)
        if not success:
            print(f"Failed to update stock {uid} with default values")
            return False

    vial_update_properties, refresh_vials = compute_stock_vial_properties(stock)

    success = edit_stock(
        username,
        uid,
        db,
        vial_update_properties,
        log_activity=False,
        refresh_vials=refresh_vials,
    )
    return success


def compute_stock_vial_properties(stock):
    """Compute the vial-refresh field updates for a stock (no database writes).

    Pure companion to :func:`update_stock_vials`: given a stock document, it
    returns ``(update_properties, refresh_vials)`` where ``update_properties``
    are the ``$set`` fields describing the refreshed vial timeline and
    ``refresh_vials`` mirrors the recursive-refresh flag the writer path uses.

    Factored out so the single-item flip path and the batched bulk flip path
    share one implementation of the vial math and cannot drift apart.
    """
    flip_frequency = float(stock["FlipFrequency"])
    developmental_time = float(stock["DevelopmentalTime"])
    vial_lifetime = float(stock["VialLifetime"])

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
        timeline = build_initial_vial_timeline(
            stock["FlipLog"],
            flip_frequency,
            developmental_time,
        )
        refresh_vials = True
    else:
        timeline = build_existing_vial_timeline(
            stock["FlipLog"],
            stock["CurrentlyAliveVials"].split(", "),
            flip_frequency,
            developmental_time,
        )
        refresh_vials = False

    currently_alive_vials, next_flip_dates, next_eclosion_dates = prune_vial_schedule(
        currently_alive_vials=timeline["currently_alive_vials"],
        source_dates=timeline["source_dates"],
        all_flip_dates=timeline["all_flip_dates"],
        next_flip_dates=timeline["next_flip_dates"],
        next_eclosion_dates=timeline["next_eclosion_dates"],
        vial_lifetime=vial_lifetime,
        last_flip_timestamp=stock["LastFlipDate"],
        flip_frequency=flip_frequency,
    )

    update_properties = build_vial_update_properties(
        currently_alive_vials,
        next_flip_dates,
        next_eclosion_dates,
        flip_log=timeline.get("flip_log"),
    )

    return update_properties, refresh_vials
