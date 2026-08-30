from tests.mongo_fakes import FakeDatabase
from flymanager.utils.phenotypes.image_catalog import (
    bump_image_revision, compile_image_catalog, read_image_revision, refresh_image_catalog)


def _entry(image_id, keys=(), order=0):
    return {"imageId": image_id, "storageId": "s", "sha256": image_id,
            "match": {"markerKeys": list(keys)}, "display": {"sortOrder": order}}


def test_compile_indexes_and_totally_orders_entries():
    snapshot = compile_image_catalog([_entry("z", ["Sb"], 0), _entry("a", ["Sb"], 0),
                                      _entry("m", ["Sb"], -1), {"imageId": "broken"}])
    assert [e["imageId"] for e in snapshot["by_marker_key"]["Sb"]] == ["m", "a", "z"]


def test_revision_controls_refresh():
    db = FakeDatabase({"settings": [], "marker_images": [_entry("a")]})
    assert read_image_revision(db) == 0
    assert bump_image_revision(db) == 1
    assert refresh_image_catalog(db, force=True)["entries"][0]["imageId"] == "a"
