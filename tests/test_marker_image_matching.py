import pytest

from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_library import select_phenotype_reference_images


@pytest.fixture(autouse=True)
def _restore_snapshot():
    """The snapshot is process-global; without this it leaks between modules."""
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "revision": -1})


def _entry(image_id, *, keys=(), stem="", body="wing"):
    return {"imageId": image_id, "storageId": "s", "sha256": "0" * 64,
            "match": {"markerKeys": list(keys), "aliases": [], "stem": stem,
                      "bodyPart": body, "manifestEntry": False, "sourceCollection": ""},
            "display": {"sortOrder": 0, "priority": 0}}


def test_exact_binding_beats_fuzzy_and_returns_content_url():
    image_catalog.set_image_catalog_for_testing(image_catalog.compile_image_catalog([
        _entry("img_upload", keys=["Sb[1]"]), _entry("img_fuzzy", stem="sb")]))
    matches = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"}])
    assert [m["image_id"] for m in matches] == ["img_upload"]
    assert matches[0]["image_url"] == "/markers/images/img_upload"


def test_body_part_gate_excludes_mismatch():
    """The marker still gets a row; it just gets no image.

    The selector now emits one row per marker so nothing vanishes from a
    phenotype view, so "excluded" means has_image is False rather than an
    empty result.
    """
    image_catalog.set_image_catalog_for_testing(image_catalog.compile_image_catalog([
        _entry("img_eye", stem="sb", body="eye")]))
    rows = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"}])
    assert [r["has_image"] for r in rows] == [False]
    assert rows[0]["image_id"] is None


def test_upload_binds_to_a_real_resolver_marker_not_a_hand_made_stub():
    """Regression: the exact-key tier was dead code on the real path.

    Resolved markers carry no "key" field -- get_visual_marker returns
    gene_stem/allele_token instead -- so a test that injects "key" by hand
    proves only that the arithmetic works. Drive it with what the resolver
    actually emits, which is how the bug hid.
    """
    from flymanager.utils.phenotypes.visual_markers import get_visual_marker

    marker = get_visual_marker("Sb")
    assert marker is not None, "Sb must resolve or this test means nothing"
    assert "key" not in marker, (
        "resolver now stamps 'key'; _marker_definition_keys can be simplified")

    image_catalog.set_image_catalog_for_testing(image_catalog.compile_image_catalog([
        _entry("img_upload", keys=["Sb"]),
        _entry("img_fuzzy", stem="sb", body=marker.get("body_part", "bristle")),
    ]))
    assert [m["image_id"] for m in select_phenotype_reference_images([marker])] \
        == ["img_upload"]


def test_allele_marker_binds_through_its_allele_token():
    from flymanager.utils.phenotypes.visual_markers import get_visual_marker

    marker = get_visual_marker("Bl", allele_spec="1")
    assert marker is not None and marker.get("allele_token") == "Bl[1]"

    image_catalog.set_image_catalog_for_testing(image_catalog.compile_image_catalog([
        _entry("img_upload", keys=["Bl[1]"])]))
    assert [m["image_id"] for m in select_phenotype_reference_images([marker])] \
        == ["img_upload"]
