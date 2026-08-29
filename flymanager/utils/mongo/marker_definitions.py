"""CRUD for the marker_definitions overlay collection.

Mongo holds deltas only: user-created definitions and explicit overrides of a
shipped key. The shipped layer (data/markers/catalog.json) is never written
here. Every write bumps markerCatalogRevision, which is what makes other
worker processes converge on the change.
"""
from copy import deepcopy
from datetime import datetime, timezone

from pymongo import ReturnDocument

from flymanager.utils.mongo.activity import write_activity
from flymanager.utils.phenotypes.marker_catalog import (
    MARKER_CATALOG_REVISION_KEY, MARKER_DEFINITIONS_COLLECTION,
    load_shipped_catalog, validate_definition)

ADMIN_USERNAME = "admin"

MUTABLE_FIELDS = ("kind", "match", "payload", "sorting", "audit", "imaging",
                  "expression", "provenance")


class MarkerDefinitionError(Exception):
    """A marker definition write that a route should turn into an HTTP status."""

    def __init__(self, message, *, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _shipped_definitions():
    catalog = load_shipped_catalog()
    return ({definition["Key"]: definition for definition in catalog["definitions"]},
            catalog["catalogVersion"])


def bump_marker_catalog_revision(db):
    """Atomically increment markerCatalogRevision and return the new value.

    Was read-modify-write through settings.update_settings, which (a)
    swallows any exception and returns False with nobody checking it, so a
    failed bump left other worker processes never converging on the edit
    while the rebuild job (force=True) stamped the new signature anyway, and
    (b) is not atomic: two concurrent writers can both read revision N and
    both write N+1, losing a bump. $inc against the collection directly is
    atomic and self-verifying -- find_one_and_update either returns the
    incremented document or raises, there is no silent False to ignore.
    """
    result = db["settings"].find_one_and_update(
        {},
        {"$inc": {MARKER_CATALOG_REVISION_KEY: 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not isinstance(result, dict) or MARKER_CATALOG_REVISION_KEY not in result:
        raise RuntimeError(
            "Failed to bump markerCatalogRevision: find_one_and_update "
            f"returned {result!r}")
    return int(result[MARKER_CATALOG_REVISION_KEY])


def can_edit_marker_definition(document, username):
    """Whether `username` may edit/delete this row as it stands right now.

    Shipped rows are never editable in place. A curated row is admin-only.
    Otherwise (origin == "user") the creator or admin may edit it.
    """
    origin = (document or {}).get("origin")
    if origin == "shipped":
        return False
    if username == ADMIN_USERNAME:
        return True
    if origin == "curated":
        return False
    return str((document or {}).get("CreatedBy") or "") == str(username or "")


def get_marker_definition(db, key):
    """The effective definition for a key: overlay if present, else shipped."""
    key = str(key or "").strip()
    overlay = db[MARKER_DEFINITIONS_COLLECTION].find_one({"Key": key})
    if overlay is not None:
        overlay = dict(overlay)
        overlay.pop("_id", None)
        overlay["origin"] = overlay.get("origin") or "user"
        return overlay
    shipped, _ = _shipped_definitions()
    definition = shipped.get(key)
    if definition is None:
        return None
    definition = deepcopy(definition)
    definition["origin"] = "shipped"
    return definition


def _overlay_document(db, key):
    return db[MARKER_DEFINITIONS_COLLECTION].find_one({"Key": str(key or "").strip()})


def _normalized(document):
    normalized = {field: deepcopy(document.get(field))
                  for field in MUTABLE_FIELDS if field in document}
    normalized.setdefault("sorting", {})
    normalized.setdefault("audit", {})
    normalized.setdefault("imaging", {"aliases": [], "images": []})
    normalized.setdefault("expression", {})
    normalized.setdefault("provenance", {})
    return normalized


def create_marker_definition(db, document, *, username):
    key = str((document or {}).get("Key") or "").strip()
    if not key:
        raise MarkerDefinitionError("Key is required", status_code=400)

    candidate = dict(_normalized(document), Key=key)
    errors = validate_definition(candidate)
    if errors:
        raise MarkerDefinitionError("; ".join(errors), status_code=400)

    if _overlay_document(db, key) is not None:
        raise MarkerDefinitionError(f"A definition for {key} already exists", status_code=409)

    shipped, shipped_version = _shipped_definitions()
    now = _timestamp()
    candidate.update({
        "origin": "user",
        "CreatedBy": username,
        "CreatedAt": now,
        "UpdatedBy": username,
        "UpdatedAt": now,
    })
    if key in shipped:
        candidate["overridesShipped"] = {"key": key, "shippedVersion": shipped_version}

    db[MARKER_DEFINITIONS_COLLECTION].insert_one(candidate)
    bump_marker_catalog_revision(db)
    write_activity(username, f"Created marker definition {key}", db)
    return candidate


def _require_editable(db, key, username):
    """Load the overlay row for `key` and confirm `username` may write to it.

    A shipped key with no overlay row is reported as 409, not 404: the key
    does exist, it is just not editable in place -- the caller has to create
    an override instead.
    """
    existing = _overlay_document(db, key)
    if existing is None:
        shipped, _ = _shipped_definitions()
        if key in shipped:
            raise MarkerDefinitionError(
                f"{key} is a shipped definition; create an override instead",
                status_code=409)
        raise MarkerDefinitionError(f"No definition for {key}", status_code=404)
    if not can_edit_marker_definition(existing, username):
        raise MarkerDefinitionError(f"You cannot edit {key}", status_code=403)
    return existing


def update_marker_definition(db, key, document, *, username):
    key = str(key or "").strip()
    existing = _require_editable(db, key, username)

    candidate = dict(existing)
    candidate.pop("_id", None)  # never $set the immutable _id on a real Mongo
    candidate.update(_normalized(document))
    candidate["Key"] = key
    errors = validate_definition(candidate)
    if errors:
        raise MarkerDefinitionError("; ".join(errors), status_code=400)

    candidate["UpdatedBy"] = username
    candidate["UpdatedAt"] = _timestamp()

    db[MARKER_DEFINITIONS_COLLECTION].update_one({"Key": key}, {"$set": candidate})
    bump_marker_catalog_revision(db)
    write_activity(username, f"Updated marker definition {key}", db)
    return candidate


def delete_marker_definition(db, key, *, username):
    key = str(key or "").strip()
    existing = _require_editable(db, key, username)
    restored_shipped = bool(existing.get("overridesShipped"))

    db[MARKER_DEFINITIONS_COLLECTION].delete_one({"Key": key})
    bump_marker_catalog_revision(db)
    write_activity(username, f"Deleted marker definition {key}", db)
    return {"key": key, "restored_shipped": restored_shipped}


def promote_marker_definition(db, key, *, username):
    key = str(key or "").strip()
    if username != ADMIN_USERNAME:
        raise MarkerDefinitionError("Admin access required", status_code=403)

    existing = _overlay_document(db, key)
    if existing is None:
        raise MarkerDefinitionError(f"No definition for {key}", status_code=404)

    now = _timestamp()
    updates = {"origin": "curated", "CuratedBy": username, "CuratedAt": now}
    db[MARKER_DEFINITIONS_COLLECTION].update_one({"Key": key}, {"$set": updates})
    bump_marker_catalog_revision(db)
    write_activity(username, f"Promoted marker definition {key} to curated", db)
    return dict(existing, **updates)


def _search_haystack(document):
    return " ".join(str(value) for value in (
        document.get("Key"),
        (document.get("payload") or {}).get("display_label"),
        (document.get("payload") or {}).get("effect"),
        (document.get("payload") or {}).get("body_part"),
    ) if value)


def list_marker_definitions(db, *, kind=None, origin=None, search=None, username=None):
    """The merged shipped+overlay catalog view, newest layer winning by Key.

    Each row carries `origin` and `editable_by_user`: whether THIS VIEWER
    (`username`) may edit this particular row right now, per
    `can_edit_marker_definition`. It is viewer-scoped, not a static property
    of the row's origin -- a curated row is editable by admin but not by its
    original creator, and a user row is editable by its creator or admin but
    not by anyone else. Task 15's list view renders an Edit action straight
    off this flag, so it must answer "would a write by this viewer succeed",
    not "is this a user-origin row" -- otherwise the UI offers an action
    that 403s, or hides one that would have worked.
    """
    shipped, _ = _shipped_definitions()
    merged = {key: dict(deepcopy(definition), origin="shipped")
              for key, definition in shipped.items()}
    for document in db[MARKER_DEFINITIONS_COLLECTION].find({}):
        document = dict(document)
        document.pop("_id", None)
        document.setdefault("origin", "user")
        merged[str(document.get("Key") or "").strip()] = document

    rows = []
    for key in sorted(merged):
        document = dict(merged[key])
        document["editable_by_user"] = can_edit_marker_definition(document, username)
        if kind and document.get("kind") != kind:
            continue
        if origin and document.get("origin") != origin:
            continue
        if search and search.lower() not in _search_haystack(document).lower():
            continue
        rows.append(document)
    return rows
