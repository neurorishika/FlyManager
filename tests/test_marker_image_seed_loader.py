import hashlib
import json

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.phenotypes.image_seed import (load_image_seed,
                                                    upsert_image_entry)
from flymanager.utils.phenotypes.image_store import MemoryImageStore


def test_seed_load_is_idempotent(tmp_path):
    data = b"fake-webp"
    digest = hashlib.sha256(data).hexdigest()
    record = {"imageId": f"img_{digest[:16]}", "file": "one.webp", "sha256": digest,
              "contentType": "image/webp", "bytes": len(data), "width": 1, "height": 1,
              "match": {"markerKeys": []}, "display": {}, "origin": "shipped"}
    (tmp_path / "one.webp").write_bytes(data)
    (tmp_path / "index.json").write_text(json.dumps([record]))
    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()
    assert load_image_seed(db, store, seed_dir=tmp_path) == 1
    assert load_image_seed(db, store, seed_dir=tmp_path) == 0
    assert len(list(db.marker_images.find({}))) == 1


def _normalized(color=(3, 4, 5)):
    import io

    from PIL import Image

    from flymanager.utils.phenotypes.image_normalize import normalize_image

    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(buffer, format="PNG")
    return normalize_image(buffer.getvalue())


def _fresh():
    return FakeDatabase({"settings": [], "marker_images": []}), MemoryImageStore()


def test_upsert_creates_a_user_entry():
    db, store = _fresh()
    entry, created, bound = upsert_image_entry(
        db, store, _normalized(), marker_key="Sb", uploaded_by="alice")
    assert (created, bound) == (True, True)
    assert entry["match"]["markerKeys"] == ["Sb"]
    assert entry["origin"] == "user"
    assert store.exists(entry["storageId"])


def test_upsert_binds_identical_bytes_to_a_second_marker_without_duplicating():
    db, store = _fresh()
    normalized = _normalized()
    upsert_image_entry(db, store, normalized, marker_key="Sb", uploaded_by="alice")
    entry, created, bound = upsert_image_entry(
        db, store, normalized, marker_key="CyO", uploaded_by="bob")

    assert (created, bound) == (False, True)
    assert len(list(db.marker_images.find({}))) == 1
    assert sorted(entry["match"]["markerKeys"]) == ["CyO", "Sb"]


def test_upsert_reports_a_no_op_when_already_bound_to_this_marker():
    db, store = _fresh()
    normalized = _normalized()
    upsert_image_entry(db, store, normalized, marker_key="Sb", uploaded_by="alice")
    _, created, bound = upsert_image_entry(
        db, store, normalized, marker_key="Sb", uploaded_by="alice")

    # Neither created nor newly bound: the route must not claim "uploaded".
    assert (created, bound) == (False, False)


def test_upsert_keeps_a_new_caption_on_re_upload():
    db, store = _fresh()
    normalized = _normalized()
    upsert_image_entry(db, store, normalized, marker_key="Sb",
                       uploaded_by="alice", display={"caption": "first"})
    entry, _, _ = upsert_image_entry(db, store, normalized, marker_key="Sb",
                                     uploaded_by="alice",
                                     display={"caption": "corrected"})
    assert entry["display"]["caption"] == "corrected"
