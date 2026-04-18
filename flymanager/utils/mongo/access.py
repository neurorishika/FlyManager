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


def get_accessible_documents(collection_name, user, db, annotate=False):
    collection = db[collection_name]
    owned_documents = list(collection.find({"User": user}))
    assigned_documents = list(collection.find({"AssignedTo": user}))
    documents = _dedupe_documents(owned_documents + assigned_documents)
    if annotate:
        return [annotate_document_access(document, user) for document in documents]
    return documents


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


def get_accessible_stocks(user, db, annotate=False):
    return get_accessible_documents("stocks", user, db, annotate=annotate)


def get_accessible_crosses(user, db, annotate=False):
    return get_accessible_documents("crosses", user, db, annotate=annotate)


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
    return [
        _normalize_username(profile.get("Username"))
        for profile in get_user_profiles(db)
        if _normalize_username(profile.get("ReportsTo")) == user
    ]


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


def update_document_assignment(collection_name, owner, uid, assignee, db):
    collection = db[collection_name]
    current_document = collection.find_one({"UniqueID": uid, "User": owner})
    if not current_document:
        return False, "Record not found."

    normalized_assignee = _normalize_username(assignee)
    if not can_assign_to_user(owner, normalized_assignee, db):
        return False, "Assignee must be one of your direct reports."

    stored_assignee = ""
    if normalized_assignee and normalized_assignee != owner:
        stored_assignee = normalized_assignee

    if _normalize_username(current_document.get("AssignedTo")) == stored_assignee:
        return True, None

    timestamp = current_timestamp()
    if stored_assignee:
        assignment_detail = f"assigned to {stored_assignee}"
    else:
        assignment_detail = "returned to owner maintenance"

    modification_entry = f"{timestamp} : Assignment updated to {assignment_detail}"
    modification_log = current_document.get("ModificationLog", "")

    update_fields = {
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

    collection.update_one({"UniqueID": uid, "User": owner}, {"$set": update_fields})
    return True, None