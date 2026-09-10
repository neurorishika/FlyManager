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
from flymanager.utils.cache_generation import (
    pending_cache_generation, schedule_record_cache_generation,
    stock_cache_signature)

import logging
import time

logger = logging.getLogger(__name__)


def build_stock_standardization_cache(genotype):
    # Imported lazily: the standardization service lives under
    # ``flymanager.app.services`` and importing it at module load would pull the
    # app package in during MongoDB bootstrap, creating a circular import.
    from flymanager.app.services.stock_standardization import \
        build_stock_standardization_cache as _build
    return _build(genotype)


def add_to_stock(user, properties, db, submission_key=None):
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

    # A browser can lose the redirect after the insert commits.  Reusing the
    # per-form submission key returns that committed stock instead of creating
    # a second record on retry.
    submission_key = str(submission_key or "").strip()
    if submission_key:
        existing = db["stocks"].find_one({
            "User": user,
            "SubmissionKey": submission_key,
        })
        if existing:
            generation = existing.get("CacheGeneration") or {}
            if generation.get("state") != "ready":
                schedule_record_cache_generation(
                    db,
                    actor=user,
                    record_type="stock",
                    unique_id=existing["UniqueID"],
                    input_signature=generation.get("inputSignature") or stock_cache_signature(
                        existing.get("Genotype", ""),
                    ),
                )
            return True, existing["UniqueID"]

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

    input_signature = stock_cache_signature(genotype)
    stock_document = build_owned_document(
        user=user,
        uid=uid,
        properties=properties,
        required_properties=REQUIRED_STOCK_PROPERTIES,
        optional_properties=OPTIONAL_STOCK_PROPERTIES,
        base_document={
            "Genotype": genotype,
            # Do not build these inside a user request.  The RQ worker fills
            # them after this minimal, durable record has committed.
            "PhenotypeCache": None,
            "StandardizationCache": None,
            "CacheGeneration": pending_cache_generation(
                "stock", uid, input_signature,
            ),
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
    if submission_key:
        stock_document["SubmissionKey"] = submission_key

    # insert the document into the MongoDB collection
    stocks_collection = db["stocks"]
    insert_started_at = time.perf_counter()
    try:
        stocks_collection.insert_one(stock_document)
    except Exception as exc:
        # The unique sparse index turns two simultaneous retries of the same
        # form into one committed stock.  Avoid importing pymongo's exception
        # class here so in-memory test databases remain lightweight.
        if submission_key and exc.__class__.__name__ == "DuplicateKeyError":
            existing = stocks_collection.find_one({
                "User": user,
                "SubmissionKey": submission_key,
            })
            if existing:
                return True, existing["UniqueID"]
        raise

    logger.info(
        "Stock persisted uid=%s mongo_insert_ms=%.1f cache_state=pending",
        uid, (time.perf_counter() - insert_started_at) * 1000,
    )

    schedule_record_cache_generation(
        db,
        actor=user,
        record_type="stock",
        unique_id=uid,
        input_signature=input_signature,
    )

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
        input_signature = stock_cache_signature(prepared_updates["Genotype"])
        prepared_updates["PhenotypeCache"] = None
        prepared_updates["StandardizationCache"] = None
        prepared_updates["CacheGeneration"] = pending_cache_generation(
            "stock", uid, input_signature,
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
        schedule_record_cache_generation(
            db,
            actor=user,
            record_type="stock",
            unique_id=uid,
            input_signature=input_signature,
        )
        from flymanager.utils.mongo.crosses import \
            propagate_stock_genotype_to_crosses
        summary = propagate_stock_genotype_to_crosses(
            user, uid, prepared_updates["Genotype"], db
        )
        if summary.get("errors"):
            logger.warning(
                "Genotype propagation for stock %s hit %s error(s) "
                "(crosses_updated=%s)",
                uid,
                summary["errors"],
                summary.get("crosses_updated"),
            )

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
