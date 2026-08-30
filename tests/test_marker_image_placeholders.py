"""The selector emits one row per marker, matched or not.

Two problems motivate this. A marker whose best image was already taken by
an earlier marker used to be dropped entirely, and a per-view image cap
silently truncated the rest -- so a six-marker phenotype could render three
cards with no indication anything was missing. Now every marker gets a row,
and an unmatched one carries the definition Key needed to link a user to
the page where they can upload an image for it.
"""
import pytest

from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_library import (
    select_phenotype_reference_images)


def _entry(image_id, *, keys=(), stem="", body="wing", aliases=()):
    return {
        "imageId": image_id, "storageId": "s-" + image_id, "sha256": "0" * 64,
        "contentType": "image/webp", "bytes": 1, "width": 8, "height": 8,
        "match": {"markerKeys": list(keys), "aliases": list(aliases), "stem": stem,
                  "bodyPart": body, "manifestEntry": False, "sourceCollection": ""},
        "display": {"sortOrder": 0, "priority": 0, "sourceName": "Test Set"},
        "origin": "user",
    }


def _install(entries):
    image_catalog.set_image_catalog_for_testing(
        image_catalog.compile_image_catalog(entries))


@pytest.fixture(autouse=True)
def _restore_snapshot():
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1})


def test_unmatched_marker_still_gets_a_row():
    _install([_entry("img_cy", stem="cy", body="wing")])
    rows = select_phenotype_reference_images([
        {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing"},
        {"display_label": "Sp", "phenotype_key": "wg_Sp", "body_part": "wing",
         "allele_token": "wg[Sp-1]"},
    ])
    assert [r["display_label"] for r in rows] == ["Cy", "Sp"]
    assert [r["has_image"] for r in rows] == [True, False]
    assert rows[1]["image_id"] is None
    assert rows[1]["image_url"] is None


def test_unmatched_marker_carries_its_definition_key_for_the_upload_link():
    _install([])
    rows = select_phenotype_reference_images([
        {"display_label": "Sp", "phenotype_key": "wg_Sp", "body_part": "wing",
         "allele_token": "wg[Sp-1]"},
    ])
    assert rows[0]["marker_key"] == "wg[Sp-1]"


def test_marker_key_is_none_when_no_definition_exists():
    """Then the UI must link to the catalog, not to a 404 detail page."""
    _install([])
    rows = select_phenotype_reference_images([
        {"display_label": "Nonesuch", "phenotype_key": "Nonesuch"},
    ])
    assert rows[0]["marker_key"] is None
    assert rows[0]["has_image"] is False


def test_two_markers_sharing_a_best_image_both_get_a_row():
    """Previously the second marker was dropped, losing it from the view."""
    _install([_entry("img_shared", stem="tm6b", body="body", aliases=["tm6b"])])
    rows = select_phenotype_reference_images([
        {"display_label": "Tb", "phenotype_key": "Tb", "body_part": "body",
         "alias_token": "tm6b"},
        {"display_label": "e", "phenotype_key": "e", "body_part": "body",
         "alias_token": "tm6b"},
    ])
    assert [r["display_label"] for r in rows] == ["Tb", "e"]
    assert all(r["image_id"] == "img_shared" for r in rows)


def test_no_cap_by_default_so_nothing_is_silently_truncated():
    _install([_entry(f"img_{i}", stem=f"m{i}", body="wing") for i in range(8)])
    markers = [{"display_label": f"m{i}", "phenotype_key": f"m{i}",
                "body_part": "wing"} for i in range(8)]
    rows = select_phenotype_reference_images(markers)
    assert len(rows) == 8


def test_an_explicit_limit_is_still_honoured():
    _install([_entry(f"img_{i}", stem=f"m{i}", body="wing") for i in range(8)])
    markers = [{"display_label": f"m{i}", "phenotype_key": f"m{i}",
                "body_part": "wing"} for i in range(8)]
    assert len(select_phenotype_reference_images(markers, limit=3)) == 3


def test_duplicate_markers_are_still_collapsed():
    _install([_entry("img_cy", stem="cy", body="wing")])
    rows = select_phenotype_reference_images([
        {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing"},
        {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing"},
    ])
    assert len(rows) == 1


def test_matched_rows_keep_their_existing_fields():
    """Templates and the parity fixture depend on these staying put."""
    _install([_entry("img_cy", stem="cy", body="wing")])
    row = select_phenotype_reference_images([
        {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing",
         "effect": "curly wings"},
    ])[0]
    for field in ("image_id", "image_url", "display_label", "body_part",
                  "effect", "source_collection", "source_name", "provenance",
                  "credit", "source_url", "notes", "match_score"):
        assert field in row, field
    assert row["image_url"] == "/markers/images/img_cy"
    assert row["match_score"] > 0


def test_unmatched_row_has_zero_score_and_keeps_marker_metadata():
    _install([])
    row = select_phenotype_reference_images([
        {"display_label": "Sp", "phenotype_key": "wg_Sp", "body_part": "wing",
         "effect": "star-shaped bristles"},
    ])[0]
    assert row["match_score"] == 0
    assert row["body_part"] == "wing"
    assert row["effect"] == "star-shaped bristles"
