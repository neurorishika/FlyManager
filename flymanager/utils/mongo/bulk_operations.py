"""Batched implementations of the explorer bulk actions (flip / status change /
remove-from-tray).

The per-item routes used to issue roughly a dozen MongoDB round-trips *per
selected record* (accessibility lookups against both collections, a flip
write, a vial-refresh write, an activity insert, ...). At tens or hundreds of
selected records that serialised into tens of seconds and blew the request
timeout.

These functions collapse the work into a handful of round-trips regardless of
selection size:

* one ``$in`` read per collection to resolve accessible records,
* the field math computed in memory reusing the exact same pure helpers the
  single-item path uses (so the resulting documents are identical), and
* a single ``bulk_write`` per collection plus one ``insert_many`` for the
  activity log.

They contain no request/response or job concerns and are safe to call from a
background worker task.
"""
import datetime

from pymongo import UpdateOne

from flymanager.app.settings import (DEFAULT_CROSS_PROPERTY_VALUES,
                                     DEFAULT_STOCK_PROPERTY_VALUES,
                                     REQUIRED_CROSS_PROPERTIES,
                                     REQUIRED_STOCK_PROPERTIES)
from flymanager.utils.mongo.crosses import compute_cross_vial_properties
from flymanager.utils.mongo.stocks import compute_stock_vial_properties
from flymanager.utils.mongo.trays import move_item_to_tray
from flymanager.utils.mongo_records import (_build_flip_update_fields,
                                            _normalize_flip_timestamp,
                                            build_owned_document_update_fields,
                                            get_missing_required_updates,
                                            parse_flip_timestamp_precise,
                                            seconds_since_last_flip)


def _accessible_map(collection, user, uids):
    """Resolve which of ``uids`` are accessible in ``collection`` for ``user``.

    Mirrors the priority in :func:`get_accessible_document`: a record the user
    owns wins over one merely assigned to them. Two batched reads at most.
    """
    owned = {
        document["UniqueID"]: document
        for document in collection.find({"UniqueID": {"$in": uids}, "User": user})
    }
    remaining = [uid for uid in uids if uid not in owned]
    if remaining:
        for document in collection.find(
            {"UniqueID": {"$in": remaining}, "AssignedTo": user}
        ):
            owned.setdefault(document["UniqueID"], document)
    return owned


def _activity_document(user, activity):
    """Build one activity-log document identical to :func:`write_activity`."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return {"user": user, "timestamp": timestamp, "activity": activity}


def _compute_flip_set(document, normalized_timestamp, new_status, comment,
                      vial_properties_fn, required_properties, default_values):
    """Compute the merged ``$set`` for flipping one record.

    Equivalent to ``flip_owned_document`` followed by ``update_*_vials`` for the
    common (existing-vials) case: apply the flip fields to an in-memory copy,
    fill any missing required defaults, then overlay the recomputed vial
    timeline - vial fields win, exactly as the sequential writes ordered them.
    """
    flip_fields = _build_flip_update_fields(
        document,
        normalized_timestamp,
        new_status=new_status,
        added_comment=comment or None,
    )
    flipped = {**document, **flip_fields}
    default_updates = get_missing_required_updates(
        flipped, required_properties, default_values
    )
    working = {**flipped, **default_updates}
    vial_properties, _refresh_vials = vial_properties_fn(working)
    return {**flip_fields, **default_updates, **vial_properties}


def _changed_fields(document, updates):
    """Return only values that would materially change ``document``."""
    return {
        key: value
        for key, value in updates.items()
        if key not in document or document.get(key) != value
    }


def refresh_vial_timelines_bulk(user, stocks, crosses, db):
    """Refresh all login-time vial timelines with at most two writes.

    The previous login path called ``update_*_vials`` once per record. Each
    call read the document again and then wrote it, even when the computed
    timeline was unchanged. Here we reuse the documents already fetched for
    the login warning, perform the same calculations in memory, and batch only
    records whose persisted fields actually need changing.
    """
    collection_specs = (
        (
            "stocks",
            stocks,
            compute_stock_vial_properties,
            REQUIRED_STOCK_PROPERTIES,
            DEFAULT_STOCK_PROPERTY_VALUES,
        ),
        (
            "crosses",
            crosses,
            compute_cross_vial_properties,
            REQUIRED_CROSS_PROPERTIES,
            DEFAULT_CROSS_PROPERTY_VALUES,
        ),
    )
    refreshed = {"stocks": 0, "crosses": 0}

    for collection_name, documents, compute_vials, required, defaults in collection_specs:
        operations = []
        for document in documents:
            default_updates = get_missing_required_updates(
                document, required, defaults
            )
            working = {**document, **default_updates}
            vial_properties, _refresh_vials = compute_vials(working)
            updates = _changed_fields(
                document, {**default_updates, **vial_properties}
            )
            if updates:
                operations.append(
                    UpdateOne(
                        {"UniqueID": document["UniqueID"], "User": user},
                        {"$set": updates},
                    )
                )

        if operations:
            db[collection_name].bulk_write(operations, ordered=False)
        refreshed[collection_name] = len(operations)

    return refreshed


def bulk_flip_records(user, uids, db, flip_time, *, new_status=None, comment="",
                      progress_cb=None):
    """Flip many stocks/crosses in a batched, timeout-resistant way.

    Returns ``{"success": [...], "failed": [...]}`` matching the shape the old
    inline route produced.
    """
    normalized_timestamp = _normalize_flip_timestamp(flip_time, accept_datetime=True)
    flip_time_dt = parse_flip_timestamp_precise(normalized_timestamp)

    # Same 12-hour "don't double-flip" rule the single-item /flip route
    # enforces against LastFlipDate - bulk_flip_records used to skip this
    # entirely, which let a re-run/duplicate bulk flip silently append a
    # second vial to every already-flipped record in the selection.
    from flymanager.app import MIN_FLIP_DIFFERENCE

    results = {"success": [], "failed": []}
    stock_map = _accessible_map(db["stocks"], user, uids)
    cross_map = _accessible_map(db["crosses"], user, uids)

    stock_ops = []
    cross_ops = []
    activity_documents = []
    total = len(uids)

    for index, uid in enumerate(uids):
        try:
            if uid in stock_map:
                item_type, document, collection_key = "Stock", stock_map[uid], "stock"
            elif uid in cross_map:
                item_type, document, collection_key = "Cross", cross_map[uid], "cross"
            else:
                results["failed"].append(
                    {"uid": uid, "reason": "UID not recognized for this user."}
                )
                if progress_cb is not None:
                    progress_cb(index + 1, total)
                continue

            difference = seconds_since_last_flip(
                document.get("LastFlipDate"), flip_time_dt
            )
            if difference is not None and difference < MIN_FLIP_DIFFERENCE:
                results["failed"].append(
                    {
                        "uid": uid,
                        "reason": (
                            f"{item_type} already flipped recently at: "
                            f"{document['LastFlipDate']}"
                        ),
                    }
                )
                if progress_cb is not None:
                    progress_cb(index + 1, total)
                continue

            if collection_key == "stock":
                update_set = _compute_flip_set(
                    document, normalized_timestamp, new_status, comment,
                    compute_stock_vial_properties,
                    REQUIRED_STOCK_PROPERTIES, DEFAULT_STOCK_PROPERTY_VALUES,
                )
                stock_ops.append(
                    UpdateOne(
                        {"UniqueID": uid, "User": document["User"]},
                        {"$set": update_set},
                    )
                )
                activity_documents.append(
                    _activity_document(user, f"Flipped stock {uid} (bulk operation)")
                )
            else:
                update_set = _compute_flip_set(
                    document, normalized_timestamp, new_status, comment,
                    compute_cross_vial_properties,
                    REQUIRED_CROSS_PROPERTIES, DEFAULT_CROSS_PROPERTY_VALUES,
                )
                cross_ops.append(
                    UpdateOne(
                        {"UniqueID": uid, "User": document["User"]},
                        {"$set": update_set},
                    )
                )
                activity_documents.append(
                    _activity_document(user, f"Flipped cross {uid} (bulk operation)")
                )
            results["success"].append({"uid": uid, "type": item_type})
        except Exception as exc:  # noqa: BLE001 - per-record isolation, matches old route
            results["failed"].append({"uid": uid, "reason": str(exc)})

        if progress_cb is not None:
            progress_cb(index + 1, total)

    if stock_ops:
        db["stocks"].bulk_write(stock_ops, ordered=False)
    if cross_ops:
        db["crosses"].bulk_write(cross_ops, ordered=False)
    if activity_documents:
        db["activity"].insert_many(activity_documents)

    return results


def bulk_change_status_records(user, uids, db, status, *, comment=None,
                               progress_cb=None):
    """Change the status (and optionally append a comment) on many records."""
    results = {"success": [], "failed": []}
    stock_map = _accessible_map(db["stocks"], user, uids)
    cross_map = _accessible_map(db["crosses"], user, uids)

    stock_ops = []
    cross_ops = []
    activity_documents = []
    total = len(uids)

    for index, uid in enumerate(uids):
        try:
            if uid in stock_map:
                document = stock_map[uid]
                updates = _status_updates(document, status, comment)
                update_set = build_owned_document_update_fields(document, updates)
                stock_ops.append(
                    UpdateOne(
                        {"UniqueID": uid, "User": document["User"]},
                        {"$set": update_set},
                    )
                )
                activity_documents.append(
                    _activity_document(
                        user,
                        f"Changed status of stock {uid} to {status} (bulk operation)",
                    )
                )
                results["success"].append({"uid": uid, "type": "Stock"})
            elif uid in cross_map:
                document = cross_map[uid]
                updates = _status_updates(document, status, comment)
                update_set = build_owned_document_update_fields(document, updates)
                cross_ops.append(
                    UpdateOne(
                        {"UniqueID": uid, "User": document["User"]},
                        {"$set": update_set},
                    )
                )
                activity_documents.append(
                    _activity_document(
                        user,
                        f"Changed status of cross {uid} to {status} (bulk operation)",
                    )
                )
                results["success"].append({"uid": uid, "type": "Cross"})
            else:
                results["failed"].append(
                    {"uid": uid, "reason": "UID not recognized for this user."}
                )
        except Exception as exc:  # noqa: BLE001 - per-record isolation, matches old route
            results["failed"].append({"uid": uid, "reason": str(exc)})

        if progress_cb is not None:
            progress_cb(index + 1, total)

    if stock_ops:
        db["stocks"].bulk_write(stock_ops, ordered=False)
    if cross_ops:
        db["crosses"].bulk_write(cross_ops, ordered=False)
    if activity_documents:
        db["activity"].insert_many(activity_documents)

    return results


def _status_updates(document, status, comment):
    updates = {"Status": status}
    if comment:
        existing_comments = str(document.get("Comments", "") or "").strip()
        updates["Comments"] = (
            f"{comment}; {existing_comments}" if existing_comments else comment
        )
    return updates


def bulk_remove_from_tray_records(user, uids, item_types, db, *, progress_cb=None):
    """Remove many items (a mix of stocks and crosses) from their trays.

    ``item_types`` is a list parallel to ``uids`` - each item is removed
    according to its own type, so a single call can cover a cart that mixes
    stocks and crosses.

    Tray removal also refreshes vials (via ``edit_stock``/``edit_cross``), so
    this preserves that exact per-item behaviour through ``move_item_to_tray``
    rather than reimplementing it - the win here is running off the request
    thread in a background job, not collapsing the writes.
    """
    results = {"success": [], "failed": []}
    total = len(uids)

    for index, (uid, item_type) in enumerate(zip(uids, item_types)):
        try:
            if move_item_to_tray(user, item_type, uid, "", "", db):
                results["success"].append({"uid": uid, "type": item_type})
            else:
                results["failed"].append(
                    {"uid": uid, "reason": "Item not found or could not be removed."}
                )
        except Exception as exc:  # noqa: BLE001 - per-record isolation
            results["failed"].append({"uid": uid, "reason": str(exc)})

        if progress_cb is not None:
            progress_cb(index + 1, total)

    return results
