from flymanager.app.settings import (BASE_CROSS_PROPERTIES,
                                     DEFAULT_CROSS_PROPERTY_VALUES,
                                     OPTIONAL_CROSS_PROPERTIES,
                                     REQUIRED_CROSS_PROPERTIES)
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
from flymanager.utils.phenotypes.predictor import build_cross_phenotype_cache


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

    require_fields(properties, REQUIRED_CROSS_PROPERTIES + BASE_CROSS_PROPERTIES)

    # Create a UniqueID for the cross based on Male and Female UniqueID + User + Name
    uid = generate_unique_id(
        [user, properties["MaleUniqueID"], properties["FemaleUniqueID"], properties["Name"]],
        db,
    )

    # Get the current timestamp
    timestamp = current_timestamp()

    # make sure genotype meets the qc
    qc, male_genotype = qc_genotype(properties["MaleGenotype"])
    if not qc:
        return False, male_genotype

    qc, female_genotype = qc_genotype(properties["FemaleGenotype"])
    if not qc:
        return False, female_genotype

    cross_document = build_owned_document(
        user=user,
        uid=uid,
        properties=properties,
        required_properties=REQUIRED_CROSS_PROPERTIES,
        optional_properties=OPTIONAL_CROSS_PROPERTIES,
        base_document={
            "MaleUniqueID": properties["MaleUniqueID"],
            "FemaleUniqueID": properties["FemaleUniqueID"],
            "MaleGenotype": male_genotype,
            "FemaleGenotype": female_genotype,
            "PhenotypeCache": build_cross_phenotype_cache(
                male_genotype,
                female_genotype,
            ),
            "Name": properties["Name"],
            "TrayID": "",
            "TrayPosition": "",
            "CreationDate": timestamp,
            "DataModifiedDate": timestamp,
            "ModificationLog": f"{timestamp} : Cross created",
            "LastFlipDate": timestamp,
            "FlipLog": timestamp,
        },
    )

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

    return get_owned_document("crosses", user, uid, db, admin_include=admin_include)


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

    updated_cross = flip_owned_document(
        "crosses",
        user,
        uid,
        db,
        timestamp,
        new_status=new_status,
        added_comment=added_comment,
    )
    if updated_cross:
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

    return delete_owned_document("crosses", user, uid, db)


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

    prepared_updates = dict(updates)
    if "MaleGenotype" in prepared_updates or "FemaleGenotype" in prepared_updates:
        current_cross = get_cross(user, uid, db)
        if not current_cross:
            return False

        male_genotype = prepared_updates.get(
            "MaleGenotype",
            current_cross.get("MaleGenotype", ""),
        )
        female_genotype = prepared_updates.get(
            "FemaleGenotype",
            current_cross.get("FemaleGenotype", ""),
        )
        prepared_updates["PhenotypeCache"] = build_cross_phenotype_cache(
            male_genotype,
            female_genotype,
        )

    success, current_cross = apply_updates_to_owned_document(
        "crosses",
        user,
        uid,
        db,
        prepared_updates,
        log_activity=log_activity,
    )

    if success and refresh_vials and current_cross:
        update_cross_vials(current_cross, user, db)

    return success


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

    update_properties = get_missing_required_updates(
        cross,
        REQUIRED_CROSS_PROPERTIES,
        DEFAULT_CROSS_PROPERTY_VALUES,
    )
    if update_properties:
        success = edit_cross(username, uid, db, update_properties, log_activity=False)
        if not success:
            print(f"Failed to update cross {uid} with default values")
            return False

    flip_frequency = float(cross["FlipFrequency"])
    developmental_time = float(cross["DevelopmentalTime"])
    vial_lifetime = float(cross["VialLifetime"])
    max_cross_lifetime = float(cross["MaxCrossLifetime"])

    if "CurrentlyAliveVials" not in cross:
        timeline = build_initial_vial_timeline(
            cross["FlipLog"],
            flip_frequency,
            developmental_time,
        )
        refresh_vials = True
    else:
        timeline = build_existing_vial_timeline(
            cross["FlipLog"],
            cross["CurrentlyAliveVials"].split(", "),
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
        last_flip_timestamp=cross["LastFlipDate"],
        flip_frequency=flip_frequency,
        first_flip_date=timeline["first_flip_date"],
        max_lifetime_days=max_cross_lifetime,
    )

    update_properties = build_vial_update_properties(
        currently_alive_vials,
        next_flip_dates,
        next_eclosion_dates,
        flip_log=timeline.get("flip_log"),
    )

    success = edit_cross(
        username,
        uid,
        db,
        update_properties,
        log_activity=False,
        refresh_vials=refresh_vials,
    )
    return success
