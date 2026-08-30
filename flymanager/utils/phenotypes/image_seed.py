import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pymongo.errors import DuplicateKeyError

from flymanager.utils.phenotypes.image_catalog import bump_image_revision

logger = logging.getLogger(__name__)
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_DIR = REPO_ROOT / "data" / "markers" / "images"


def load_image_seed(db, store, *, seed_dir=None):
    base = Path(seed_dir) if seed_dir is not None else DEFAULT_SEED_DIR
    index_path = base / "index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"marker image seed index missing: {index_path}")
    records = json.loads(index_path.read_text(encoding="utf-8"))
    existing = {d.get("sha256") for d in db["marker_images"].find({}, {"sha256": 1})}
    inserted = 0
    for record in records:
        if record.get("sha256") in existing:
            continue
        data = (base / record["file"]).read_bytes()
        if len(data) != record["bytes"]:
            raise ValueError(f"marker image seed byte count mismatch: {record['file']}")
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"marker image seed hash mismatch: {record['file']}")
        storage_id = store.put(data, content_type=record["contentType"])
        document = {key: value for key, value in record.items() if key != "file"}
        document.update({"storageId": storage_id, "UploadedBy": "system",
                         "UploadedAt": datetime.now(timezone.utc)})
        try:
            db["marker_images"].insert_one(document)
        except DuplicateKeyError:
            # Another process (web app and RQ worker both call create_app)
            # seeded this entry between our find and our insert. That is the
            # loader converging, not an error -- but the bytes we just wrote
            # are now unreferenced, so drop them.
            store.delete(storage_id)
            existing.add(record["sha256"])
            continue
        except Exception:
            store.delete(storage_id)
            raise
        existing.add(record["sha256"])
        inserted += 1
    if inserted:
        bump_image_revision(db)
        logger.info("loaded %s marker image seed entries", inserted)
    return inserted


def upsert_image_entry(db, store, normalized, *, marker_key, uploaded_by, display=None):
    """Store a normalized image and bind it to a marker.

    Identity is the content hash, so re-uploading the same bytes for another
    marker appends to markerKeys rather than duplicating the entry.

    Returns (document, created, bound) so the caller can flash something
    truthful: an image already bound to this marker is a no-op, not an
    upload.
    """
    image_id = f"img_{normalized.sha256[:16]}"
    display = display or {}
    existing = db["marker_images"].find_one({"imageId": image_id})
    if existing is not None:
        keys = (existing.get("match") or {}).get("markerKeys") or []
        already_bound = marker_key in keys
        updates = {}
        # $addToSet, not a read-modify-write of the whole list: under
        # `gunicorn --threads 12` two concurrent binds of the same image
        # would otherwise each write a list computed before the other's,
        # losing a key.
        if not already_bound:
            updates["$addToSet"] = {"match.markerKeys": marker_key}
        # A re-upload carrying a new caption should not silently discard it.
        caption = str(display.get("caption") or "").strip()
        if caption and caption != (existing.get("display") or {}).get("caption"):
            updates["$set"] = {"display.caption": caption}
        if updates:
            db["marker_images"].update_one({"imageId": image_id}, updates)
            bump_image_revision(db)
        return db["marker_images"].find_one({"imageId": image_id}), False, not already_bound
    storage_id = store.put(normalized.data, content_type=normalized.content_type)
    document = {
        "imageId": image_id, "storageId": storage_id,
        "sha256": normalized.sha256, "contentType": normalized.content_type,
        "bytes": normalized.bytes, "width": normalized.width, "height": normalized.height,
        "match": {"markerKeys": [marker_key], "aliases": [], "stem": "",
                  "bodyPart": "", "manifestEntry": False, "sourceCollection": ""},
        "display": {"label": display.get("label", ""),
                    "caption": display.get("caption", ""),
                    "credit": display.get("credit", ""), "provenance": "User upload",
                    "priority": 3, "sortOrder": int(display.get("sortOrder", 0) or 0)},
        "origin": "user", "UploadedBy": uploaded_by,
        "UploadedAt": datetime.now(timezone.utc),
    }
    try:
        db["marker_images"].insert_one(document)
    except DuplicateKeyError:
        # Raced with a concurrent upload of identical bytes; bind to the
        # winner rather than failing the user's request.
        store.delete(storage_id)
        db["marker_images"].update_one(
            {"imageId": image_id},
            {"$addToSet": {"match.markerKeys": marker_key}})
        bump_image_revision(db)
        return db["marker_images"].find_one({"imageId": image_id}), False, True
    except Exception:
        store.delete(storage_id)
        raise
    bump_image_revision(db)
    return document, True, True
