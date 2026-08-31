from tests.mongo_fakes import FakeDatabase
from flymanager.utils.phenotypes.image_catalog import (
    bump_image_revision, compile_image_catalog, read_image_revision, refresh_image_catalog)


def _entry(image_id, keys=(), order=0):
    return {"imageId": image_id, "storageId": "s", "sha256": image_id,
            "match": {"markerKeys": list(keys)}, "display": {"sortOrder": order}}


def test_compile_totally_orders_entries_and_drops_incomplete_ones():
    """Ordering is still a compile-time guarantee; the by_marker_key index it
    used to feed is gone, because the only consumer that ever read it was
    the marker detail page, and every shipped image has an empty markerKeys
    so that page showed nothing. Images resolve through the scorer now."""
    snapshot = compile_image_catalog([_entry("z", ["Sb"], 0), _entry("a", ["Sb"], 0),
                                      _entry("m", ["Sb"], -1), {"imageId": "broken"}])
    assert [e["imageId"] for e in snapshot["entries"]] == ["m", "a", "z"]
    assert "by_marker_key" not in snapshot


def test_revision_controls_refresh():
    db = FakeDatabase({"settings": [], "marker_images": [_entry("a")]})
    assert read_image_revision(db) == 0
    assert bump_image_revision(db) == 1
    assert refresh_image_catalog(db, force=True)["entries"][0]["imageId"] == "a"


def test_get_image_catalog_performs_no_database_access():
    """The resolution path is deliberately database-free.

    get_catalog() has the same rule and the image catalog inherits it: the
    scorer runs on that path with no db handle in scope, so a regression
    here would put a Mongo round trip inside every phenotype prediction.
    """
    import inspect

    from flymanager.utils.phenotypes import image_catalog

    source = inspect.getsource(image_catalog.get_image_catalog)
    for forbidden in ("db", "find(", "find_one", "settings", "refresh"):
        assert forbidden not in source, (
            f"get_image_catalog references {forbidden!r}; it must only "
            "return the installed snapshot")


def test_image_matching_runs_without_any_database():
    """Belt and braces: drive the real scorer with no db in scope at all."""
    from flymanager.utils.phenotypes import image_catalog
    from flymanager.utils.phenotypes.image_library import \
        select_phenotype_reference_images

    image_catalog.set_image_catalog_for_testing(
        compile_image_catalog([_entry("img_a", ["Sb"])]))
    try:
        matches = select_phenotype_reference_images(
            [{"key": "Sb", "display_label": "Sb", "body_part": "bristle"}])
        assert [m["image_id"] for m in matches] == ["img_a"]
    finally:
        image_catalog.set_image_catalog_for_testing(
            {"entries": [], "revision": -1})
