import datetime
import math
from hashlib import shake_256

from pymongo import ReturnDocument

from flymanager.utils.utils import clean_log_entry


def current_timestamp():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def require_fields(properties, field_names):
    for field_name in field_names:
        if field_name not in properties:
            raise ValueError(f"{field_name} is required")


def _uid_exists(uid, db):
    from flymanager.utils.mongo.db import uid_exists

    return uid_exists(uid, db)


def generate_unique_id(seed_parts, db):
    uid = shake_256("".join(str(part) for part in seed_parts).encode()).hexdigest(5)

    while _uid_exists(uid, db):
        print("UniqueID already exists, generating a new one")
        uid = shake_256(uid.encode()).hexdigest(5)

    return uid


def build_owned_document(
    *,
    user,
    uid,
    properties,
    required_properties,
    optional_properties,
    base_document,
):
    document = {
        "UniqueID": uid,
        "User": user,
        **base_document,
    }

    for field_name in required_properties:
        document[field_name] = properties[field_name]

    for field_name in optional_properties:
        document[field_name] = properties.get(field_name, "")

    return document


def rebuild_flip_log(vials, flip_dates):
    return "; ".join(
        f"{vial}, {date.strftime('%Y-%m-%d %H:%M')}"
        for vial, date in zip(vials[::-1], flip_dates[::-1])
    )


def build_initial_vial_timeline(flip_log, flip_frequency, developmental_time):
    flip_dates = [clean_log_entry(flip)[1] for flip in flip_log.split(";")][::-1]
    vials = [f"V{i}" for i in range(1, len(flip_dates) + 1)]

    return {
        "currently_alive_vials": vials,
        "source_dates": flip_dates,
        "all_flip_dates": flip_dates,
        "next_flip_dates": [
            date + datetime.timedelta(days=flip_frequency) for date in flip_dates
        ],
        "next_eclosion_dates": [
            date + datetime.timedelta(days=developmental_time) for date in flip_dates
        ],
        "first_flip_date": flip_dates[0],
        "flip_log": rebuild_flip_log(vials, flip_dates),
    }


def build_existing_vial_timeline(
    flip_log,
    currently_alive_vials,
    flip_frequency,
    developmental_time,
):
    parsed_log_entries = [clean_log_entry(flip) for flip in flip_log.split(";")]
    date_map = {vial: date for vial, date in parsed_log_entries}
    all_flip_dates = [date for _, date in parsed_log_entries]
    source_dates = [date_map[vial] for vial in currently_alive_vials]

    return {
        "currently_alive_vials": currently_alive_vials,
        "source_dates": source_dates,
        "all_flip_dates": all_flip_dates,
        "next_flip_dates": [
            date + datetime.timedelta(days=flip_frequency) for date in source_dates
        ],
        "next_eclosion_dates": [
            date + datetime.timedelta(days=developmental_time) for date in source_dates
        ],
        "first_flip_date": parsed_log_entries[-1][1],
    }


def get_owned_document(collection_name, user, uid, db, admin_include=False):
    collection = db[collection_name]
    document = collection.find_one({"UniqueID": uid, "User": user})

    if not document and admin_include and user != "admin":
        document = collection.find_one({"UniqueID": uid, "User": "admin"})

    return document


def delete_owned_document(collection_name, user, uid, db):
    collection = db[collection_name]
    result = collection.delete_one({"UniqueID": uid, "User": user})
    return result.deleted_count > 0


def delete_owned_documents_if_status(collection_name, user, uids, db, *, required_status):
    """Delete every uid in `uids` owned by `user` whose Status matches
    `required_status`, in one batched read + one batched delete.

    Returns (deleted_uids, skipped_uids) — skipped covers both "not found /
    not owned" and "wrong status", matching the per-item route's behaviour
    of treating both as a skip rather than an error.
    """
    if not uids:
        return [], []

    collection = db[collection_name]
    candidates = {
        document["UniqueID"]: document
        for document in collection.find({"UniqueID": {"$in": uids}, "User": user})
    }

    deletable_uids = [
        uid for uid, document in candidates.items()
        if document.get("Status") == required_status
    ]
    skipped_uids = [uid for uid in uids if uid not in deletable_uids]

    if deletable_uids:
        collection.delete_many({"UniqueID": {"$in": deletable_uids}, "User": user})

    return deletable_uids, skipped_uids


def get_missing_required_updates(document, required_properties, default_values):
    update_fields = {}

    for field_name in required_properties:
        if field_name not in document:
            if field_name not in default_values:
                raise ValueError(f"{field_name} is required")
            update_fields[field_name] = default_values[field_name]

    return update_fields


def normalize_schedule_dates(dates):
    return [
        date.replace(hour=0, minute=0, second=0, microsecond=0)
        for date in dates
    ]


def parse_last_flip_date(timestamp_str):
    for timestamp_format in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed_timestamp = datetime.datetime.strptime(
                timestamp_str, timestamp_format
            )
            return parsed_timestamp.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        except ValueError:
            continue

    raise ValueError(f"Unsupported flip timestamp format: {timestamp_str}")


def get_dead_vial_indexes(vials, source_dates, next_flip_dates, all_flip_dates, vial_lifetime):
    dead_indexes = []
    current_time = datetime.datetime.now()

    for vial, source_date, next_flip_date in zip(vials, source_dates, next_flip_dates):
        death_date = source_date + datetime.timedelta(days=vial_lifetime)
        if current_time > death_date and any(date >= next_flip_date for date in all_flip_dates):
            dead_indexes.append(vials.index(vial))

    return dead_indexes


def filter_by_indexes(values, excluded_indexes):
    excluded_index_set = set(excluded_indexes)
    return [
        value for index, value in enumerate(values) if index not in excluded_index_set
    ]


def serialize_vial_state(vials, next_flip_dates, next_eclosion_dates):
    return (
        ", ".join(vials),
        ", ".join(date.strftime("%Y-%m-%d") for date in next_flip_dates),
        ", ".join(date.strftime("%Y-%m-%d") for date in next_eclosion_dates),
    )


def prune_vial_schedule(
    *,
    currently_alive_vials,
    source_dates,
    all_flip_dates,
    next_flip_dates,
    next_eclosion_dates,
    vial_lifetime,
    last_flip_timestamp,
    flip_frequency,
    first_flip_date=None,
    max_lifetime_days=None,
):
    normalized_all_flip_dates = normalize_schedule_dates(all_flip_dates)
    normalized_source_dates = normalize_schedule_dates(source_dates)
    normalized_next_flip_dates = normalize_schedule_dates(next_flip_dates)
    normalized_next_eclosion_dates = normalize_schedule_dates(next_eclosion_dates)

    dead_vial_indexes = get_dead_vial_indexes(
        currently_alive_vials,
        normalized_source_dates,
        normalized_next_flip_dates,
        normalized_all_flip_dates,
        vial_lifetime,
    )

    currently_alive_vials = filter_by_indexes(currently_alive_vials, dead_vial_indexes)
    normalized_next_flip_dates = filter_by_indexes(
        normalized_next_flip_dates, dead_vial_indexes
    )
    normalized_next_eclosion_dates = filter_by_indexes(
        normalized_next_eclosion_dates, dead_vial_indexes
    )

    last_flip_date = parse_last_flip_date(last_flip_timestamp)
    early_flip_threshold = last_flip_date + datetime.timedelta(
        days=math.floor(flip_frequency // 2)
    )
    normalized_next_flip_dates = [
        date for date in normalized_next_flip_dates if date > early_flip_threshold
    ]

    if max_lifetime_days is not None and first_flip_date is not None:
        max_lifetime_threshold = first_flip_date + datetime.timedelta(
            days=max_lifetime_days
        )
        normalized_next_flip_dates = [
            date for date in normalized_next_flip_dates if date <= max_lifetime_threshold
        ]

    return (
        currently_alive_vials,
        normalized_next_flip_dates,
        normalized_next_eclosion_dates,
    )


def build_vial_update_properties(
    currently_alive_vials,
    next_flip_dates,
    next_eclosion_dates,
    *,
    flip_log=None,
):
    (
        serialized_vials,
        serialized_next_flip_dates,
        serialized_next_eclosion_dates,
    ) = serialize_vial_state(
        currently_alive_vials,
        next_flip_dates,
        next_eclosion_dates,
    )

    update_properties = {
        "CurrentlyAliveVials": serialized_vials,
        "NextFlipDates": serialized_next_flip_dates,
        "NextEclosionDates": serialized_next_eclosion_dates,
    }

    if flip_log is not None:
        update_properties["FlipLog"] = flip_log

    if serialized_vials == "":
        update_properties["Status"] = "No longer maintained"

    return update_properties


def _build_modification_log_entries(updates, timestamp):
    return [f"{timestamp} : {field} updated to {value}" for field, value in updates.items()]


def apply_updates_to_owned_document(
    collection_name,
    user,
    uid,
    db,
    updates,
    log_activity=True,
):
    if not updates:
        return False, None

    collection = db[collection_name]
    current_document = collection.find_one({"UniqueID": uid, "User": user})
    if not current_document:
        return False, None

    update_fields = build_owned_document_update_fields(
        current_document, updates, log_activity=log_activity
    )

    result = collection.update_one(
        {"UniqueID": uid, "User": user}, {"$set": update_fields}
    )
    return result.matched_count > 0, current_document


def build_owned_document_update_fields(current_document, updates, *, log_activity=True, timestamp=None):
    """Compute the ``$set`` fields for an owned-document update (no writes).

    Pure helper shared by :func:`apply_updates_to_owned_document` and the
    batched bulk-operation path so both build the ModificationLog /
    DataModifiedDate bookkeeping identically.
    """
    timestamp = timestamp or current_timestamp()
    update_fields = dict(updates)
    modification_log_entries = _build_modification_log_entries(updates, timestamp)

    if log_activity and modification_log_entries:
        modification_log = current_document.get("ModificationLog", "")
        new_modification_log = "; ".join(modification_log_entries)
        update_fields["ModificationLog"] = (
            f"{new_modification_log}; {modification_log}"
            if modification_log
            else new_modification_log
        )
        update_fields["DataModifiedDate"] = timestamp

    return update_fields


def diff_candidate_against_record(current_document, field_mapping):
    """Compare a candidate's field values against a document's current values.

    Returns only fields that would actually change (after trimming, treating
    ``None``/``""`` as equivalent-empty). Each entry flags whether applying it
    would overwrite a genuinely different existing value (``conflict``) or
    only fill an empty field.
    """
    diff = {}
    for field, candidate_value in field_mapping.items():
        current_value = current_document.get(field)
        normalized_current = "" if current_value is None else str(current_value).strip()
        normalized_candidate = "" if candidate_value is None else str(candidate_value).strip()
        if normalized_current == normalized_candidate:
            continue
        diff[field] = {
            "current": normalized_current,
            "candidate": normalized_candidate,
            "conflict": bool(normalized_current),
        }
    return diff


def _normalize_flip_timestamp(timestamp, accept_datetime=False):
    if isinstance(timestamp, datetime.datetime):
        if not accept_datetime:
            raise TypeError("timestamp must be a string")
        timestamp = timestamp.isoformat()
    elif not isinstance(timestamp, str):
        raise TypeError("timestamp must be a string or datetime object")

    return timestamp.replace("T", " ")


def _build_flip_update_fields(current_document, timestamp, new_status=None, added_comment=None):
    update_fields = {"LastFlipDate": timestamp}

    flip_log = current_document.get("FlipLog", "")
    currently_alive_vials = current_document.get("CurrentlyAliveVials", "")

    if "," in flip_log:
        last_vial = int(flip_log.split(",")[0].strip()[1:])
        last_vial += 1
        flip_log = f"V{last_vial}, {timestamp}; {flip_log}"
        currently_alive_vials = f"{currently_alive_vials}, V{last_vial}"
    else:
        flip_log = f"V1, {timestamp}"
        currently_alive_vials = "V1"

    update_fields["FlipLog"] = flip_log
    update_fields["CurrentlyAliveVials"] = currently_alive_vials

    modification_log_entries = []

    if new_status and current_document.get("Status") != new_status:
        update_fields["Status"] = new_status
        update_fields["DataModifiedDate"] = timestamp
        modification_log_entries.append(
            f"{timestamp} : Status changed from {current_document.get('Status')} to {new_status}"
        )

    if added_comment:
        comments = current_document.get("Comments", "")
        update_fields["Comments"] = (
            f"{added_comment}; {comments}" if comments else added_comment
        )
        modification_log_entries.append(f"{timestamp} : Comments added: {added_comment}")

    if modification_log_entries:
        modification_log = current_document.get("ModificationLog", "")
        new_modification_log = "; ".join(modification_log_entries)
        update_fields["ModificationLog"] = (
            f"{new_modification_log}; {modification_log}"
            if modification_log
            else new_modification_log
        )

    return update_fields


def flip_owned_document(
    collection_name,
    user,
    uid,
    db,
    timestamp,
    new_status=None,
    added_comment=None,
    accept_datetime=False,
):
    collection = db[collection_name]
    current_document = collection.find_one({"UniqueID": uid, "User": user})
    if not current_document:
        return None

    normalized_timestamp = _normalize_flip_timestamp(
        timestamp, accept_datetime=accept_datetime
    )
    update_fields = _build_flip_update_fields(
        current_document,
        normalized_timestamp,
        new_status=new_status,
        added_comment=added_comment,
    )

    return collection.find_one_and_update(
        {"UniqueID": uid, "User": user},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
