from pymongo import UpdateOne

from flymanager.utils.mongo_records import current_timestamp


def _normalize_username(value):
    return str(value or "").strip()


def get_effective_maintainer(document):
    owner = _normalize_username(document.get("User"))
    assigned_to = _normalize_username(document.get("AssignedTo"))
    return assigned_to or owner


def annotate_document_access(document, viewer):
    annotated = dict(document)
    owner = _normalize_username(document.get("User"))
    assigned_to = _normalize_username(document.get("AssignedTo"))
    maintainer = get_effective_maintainer(document)

    assignment_scope = "owned"
    assignment_scope_label = "Maintain"
    assignment_scope_detail = "Owned by you"

    if owner == viewer and maintainer != viewer:
        assignment_scope = "assigned_out"
        assignment_scope_label = "Assigned Out"
        assignment_scope_detail = f"Maintained by {maintainer}"
    elif maintainer == viewer and owner != viewer:
        assignment_scope = "incoming"
        assignment_scope_label = "Maintain"
        assignment_scope_detail = f"Owned by {owner}"

    annotated.update(
        {
            "OwnerUser": owner,
            "AssignedTo": assigned_to,
            "MaintainerUser": maintainer,
            "AssignmentScope": assignment_scope,
            "AssignmentScopeLabel": assignment_scope_label,
            "AssignmentScopeDetail": assignment_scope_detail,
            "ViewerCanEdit": owner == viewer,
            "ViewerCanMaintain": maintainer == viewer,
            "ViewerOwnsRecord": owner == viewer,
            "ViewerAssignmentAlert": owner == viewer and maintainer != viewer,
        }
    )
    return annotated


def _dedupe_documents(documents):
    deduped = []
    seen_ids = set()
    for document in documents:
        uid = document.get("UniqueID")
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        deduped.append(document)
    return deduped


def get_accessible_documents(collection_name, user, db, annotate=False, projection=None):
    """Return documents the user owns or maintains.

    ``projection`` is passed through to MongoDB to limit the fields loaded. When
    supplied it must include the fields required downstream — at minimum
    ``UniqueID`` (dedupe) and ``User``/``AssignedTo`` (access annotation).
    """
    collection = db[collection_name]
    # Only forward a projection when one is supplied, so callers/fakes that
    # implement the single-argument ``find(query)`` signature keep working.
    find_args = (projection,) if projection is not None else ()
    owned_documents = list(collection.find({"User": user}, *find_args))
    assigned_documents = list(collection.find({"AssignedTo": user}, *find_args))
    documents = _dedupe_documents(owned_documents + assigned_documents)
    if annotate:
        return [annotate_document_access(document, user) for document in documents]
    return documents


def get_accessible_documents_page(
    collection_name, user, db, *, mongo_filter=None, skip=0, limit=None, projection=None,
    extra_sort_keys=("Name", "UniqueID"),
):
    """Query, sort, and paginate accessible documents at the database level.

    ``mongo_filter`` covers only deterministic (non-fuzzy) filters - callers
    that also need fuzzy free-text search should pass ``limit=None`` to get
    every matching document (still filtered + projected server-side) and
    apply the fuzzy filter + pagination themselves afterward.

    Sorts by TrayID, then TrayPosition (parsed numerically), then by
    ``extra_sort_keys`` in order. The default ``("Name", "UniqueID")``
    matches ``_stock_sort_key``'s tie-break order; callers whose Python
    sort key ties break differently (e.g. crosses, which only tie-break on
    TrayID/TrayPosition - see ``_cross_sort_key``) should pass a matching
    ``extra_sort_keys`` (``()`` for crosses) so the DB-level browse path and
    the Python-level search/selection paths agree on ordering.

    Access annotation (``annotate_document_access``) runs before
    ``projection`` is applied, so a projection strictly limits the final
    field set to exactly the requested names (dropping any annotation-only
    fields not explicitly requested), matching a real Mongo projection.

    When ``limit`` is set and the requested ``skip`` would land past the
    last page (stale bookmark, a filter that shrank the result set, a
    manually-edited URL), ``skip`` is clamped down to the start of the
    last valid page instead of returning an empty page - matching the
    pre-pushdown behavior of slicing an in-memory list after clamping the
    page number.

    Returns (items, total_count).
    """
    owner_scope = {"$or": [{"User": user}, {"AssignedTo": user}]}
    combined_filter = {"$and": [owner_scope, mongo_filter or {}]}

    total_count = db[collection_name].count_documents(combined_filter)

    if limit is not None and limit > 0 and total_count:
        max_skip = ((total_count - 1) // limit) * limit
        if skip > max_skip:
            skip = max_skip

    sort_spec = {"TrayID": 1, "_sortTrayPosition": 1}
    for field in extra_sort_keys:
        sort_spec[field] = 1

    pipeline = [
        {"$match": combined_filter},
        {"$addFields": {
            "_sortTrayPosition": {
                "$convert": {"input": "$TrayPosition", "to": "double", "onError": 0, "onNull": 0}
            },
        }},
        {"$sort": sort_spec},
    ]
    if skip:
        pipeline.append({"$skip": skip})
    if limit is not None:
        pipeline.append({"$limit": limit})

    items = list(db[collection_name].aggregate(pipeline))
    for document in items:
        document.pop("_sortTrayPosition", None)
    annotated = [annotate_document_access(document, user) for document in items]

    if projection:
        annotated = [
            {field: document.get(field) for field in projection if field in document}
            for document in annotated
        ]

    return annotated, total_count


def get_maintainable_documents(collection_name, user, db, annotate=False):
    documents = get_accessible_documents(collection_name, user, db, annotate=True)
    maintainable_documents = [
        document for document in documents if document.get("MaintainerUser") == user
    ]
    if annotate:
        return maintainable_documents
    return [dict(document) for document in maintainable_documents]


def get_accessible_document(collection_name, user, uid, db, admin_include=False, annotate=False):
    collection = db[collection_name]
    document = collection.find_one({"UniqueID": uid, "User": user})

    if not document:
        document = collection.find_one({"UniqueID": uid, "AssignedTo": user})

    if not document and admin_include and user != "admin":
        document = collection.find_one({"UniqueID": uid, "User": "admin"})

    if document and annotate:
        return annotate_document_access(document, user)
    return document


def get_maintainable_document(collection_name, user, uid, db, annotate=False):
    document = get_accessible_document(
        collection_name,
        user,
        uid,
        db,
        annotate=True,
    )
    if not document or document.get("MaintainerUser") != user:
        return None
    if annotate:
        return document
    return dict(document)


def get_accessible_stocks(user, db, annotate=False, projection=None):
    return get_accessible_documents("stocks", user, db, annotate=annotate, projection=projection)


def get_accessible_crosses(user, db, annotate=False, projection=None):
    return get_accessible_documents("crosses", user, db, annotate=annotate, projection=projection)


def get_maintainable_stocks(user, db, annotate=False):
    return get_maintainable_documents("stocks", user, db, annotate=annotate)


def get_maintainable_crosses(user, db, annotate=False):
    return get_maintainable_documents("crosses", user, db, annotate=annotate)


def get_accessible_stock(user, uid, db, admin_include=False, annotate=False):
    return get_accessible_document(
        "stocks",
        user,
        uid,
        db,
        admin_include=admin_include,
        annotate=annotate,
    )


def get_accessible_cross(user, uid, db, admin_include=False, annotate=False):
    return get_accessible_document(
        "crosses",
        user,
        uid,
        db,
        admin_include=admin_include,
        annotate=annotate,
    )


def get_user_profiles(db):
    users = []
    for user in db["users"].find({}):
        user_copy = dict(user)
        user_copy.pop("Password", None)
        users.append(user_copy)
    return sorted(users, key=lambda user: _normalize_username(user.get("Username")))


def get_direct_reports(user, db):
    normalized_user = _normalize_username(user)
    return sorted(
        _normalize_username(profile.get("Username"))
        for profile in db["users"].find({"ReportsTo": normalized_user}, {"Username": 1})
    )


def get_reporting_manager(user, db):
    user_document = db["users"].find_one({"Username": user})
    if not user_document:
        return ""
    return _normalize_username(user_document.get("ReportsTo"))


def update_user_reporting_manager(user, manager, db):
    normalized_user = _normalize_username(user)
    normalized_manager = _normalize_username(manager)
    if normalized_user == "admin":
        return False, "The admin account cannot report to another user."
    if normalized_manager == normalized_user:
        return False, "A user cannot report to themselves."
    if normalized_manager and not db["users"].find_one({"Username": normalized_manager}):
        return False, "Selected manager does not exist."

    result = db["users"].update_one(
        {"Username": normalized_user},
        {"$set": {"ReportsTo": normalized_manager}},
    )
    if result.matched_count == 0:
        return False, "User not found."
    return True, None


def can_assign_to_user(owner, assignee, db):
    normalized_owner = _normalize_username(owner)
    normalized_assignee = _normalize_username(assignee)
    if not normalized_assignee or normalized_assignee == normalized_owner:
        return True
    return normalized_assignee in set(get_direct_reports(normalized_owner, db))


def build_document_assignment_update_fields(current_document, owner, assignee):
    """Compute the $set fields for reassigning one document, or None if the
    assignment is already what was requested (no write needed).
    """
    normalized_assignee = _normalize_username(assignee)
    stored_assignee = ""
    if normalized_assignee and normalized_assignee != owner:
        stored_assignee = normalized_assignee

    if _normalize_username(current_document.get("AssignedTo")) == stored_assignee:
        return None

    timestamp = current_timestamp()
    if stored_assignee:
        assignment_detail = f"assigned to {stored_assignee}"
    else:
        assignment_detail = "returned to owner maintenance"

    modification_entry = f"{timestamp} : Assignment updated to {assignment_detail}"
    modification_log = current_document.get("ModificationLog", "")

    return {
        "AssignedTo": stored_assignee,
        "AssignmentUpdatedAt": timestamp,
        "AssignmentUpdatedBy": owner,
        "DataModifiedDate": timestamp,
        "ModificationLog": (
            f"{modification_entry}; {modification_log}"
            if modification_log
            else modification_entry
        ),
    }


def update_document_assignment(collection_name, owner, uid, assignee, db):
    collection = db[collection_name]
    current_document = collection.find_one({"UniqueID": uid, "User": owner})
    if not current_document:
        return False, "Record not found."

    if not can_assign_to_user(owner, assignee, db):
        return False, "Assignee must be one of your direct reports."

    update_fields = build_document_assignment_update_fields(current_document, owner, assignee)
    if update_fields is None:
        return True, None

    collection.update_one({"UniqueID": uid, "User": owner}, {"$set": update_fields})
    return True, None


def bulk_update_document_assignments(owner, assignee, targets, db):
    """Reassign many (collection_name, uid) targets to the same assignee.

    Mirrors update_document_assignment's per-item semantics (ownership check,
    can_assign_to_user check, identical ModificationLog text) but issues one
    find() + one bulk_write() per collection instead of one find_one() +
    one update_one() per target.

    `targets`: iterable of (collection_name, uid) tuples, all reassigned to
    the same `assignee` by the same `owner` (this is what assign_tray_route
    needs — every stock/cross in one tray moves to the same assignee).

    Returns (updated_count, error_message). error_message is set (and no
    writes happen) if `assignee` isn't a valid direct report.
    """
    if not can_assign_to_user(owner, assignee, db):
        return 0, "Assignee must be one of your direct reports."

    uids_by_collection = {}
    for collection_name, uid in targets:
        uids_by_collection.setdefault(collection_name, []).append(uid)

    updated_count = 0
    for collection_name, uids in uids_by_collection.items():
        collection = db[collection_name]
        documents = {
            document["UniqueID"]: document
            for document in collection.find({"UniqueID": {"$in": uids}, "User": owner})
        }
        operations = []
        for uid in uids:
            document = documents.get(uid)
            if not document:
                continue
            update_fields = build_document_assignment_update_fields(document, owner, assignee)
            updated_count += 1
            if update_fields is None:
                continue
            operations.append(
                UpdateOne({"UniqueID": uid, "User": owner}, {"$set": update_fields})
            )
        if operations:
            collection.bulk_write(operations, ordered=False)

    return updated_count, None