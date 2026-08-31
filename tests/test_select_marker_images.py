"""Every reference image for a catalog Key, through the shared scorer.

The marker catalog page used an exact-key index that no shipped image is in,
so it showed nothing. This is the replacement, and it deliberately returns
ALL matches rather than the single best one the phenotype views want.
"""
import pytest

from flymanager.utils.phenotypes import image_catalog, marker_catalog
from flymanager.utils.phenotypes.image_library import (ALIAS_SCORE,
                                                       select_marker_images)


@pytest.fixture(autouse=True)
def _restore():
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1})
    marker_catalog.reset_catalog()


GENE = {"Key": "Sb", "kind": "gene_marker", "match": {"symbol": "Sb"},
        "payload": {"display_label": "Sb", "body_part": "bristle",
                    "effect": "short bristles", "phenotype_key": "Sb"}}
BALANCER = {"Key": "CyO", "kind": "balancer", "match": {"symbol": "CyO"},
            "payload": {"default_markers": ["Sb"], "chromosome": 2}}


def _install_catalog(*definitions):
    marker_catalog.set_catalog(marker_catalog.compile_catalog(
        {"catalogVersion": 1, "definitions": list(definitions)}))


def _entry(image_id, *, keys=(), aliases=(), stem="", body="bristle",
           order=0, collection="library"):
    return {"imageId": image_id, "storageId": "s", "sha256": "0" * 64,
            "match": {"markerKeys": list(keys), "aliases": list(aliases),
                      "stem": stem, "bodyPart": body, "manifestEntry": False,
                      "sourceCollection": collection},
            "display": {"sortOrder": order, "priority": 0, "caption": "cap",
                        "credit": "cred", "provenance": "prov",
                        "sourceName": "src"}}


def _install_images(*entries):
    image_catalog.set_image_catalog_for_testing(
        image_catalog.compile_image_catalog(list(entries)))


def test_an_alias_bound_seed_image_is_found_for_its_marker():
    """The regression test for the reported bug: every shipped image has an
    empty markerKeys and was therefore invisible on the marker page."""
    _install_catalog(GENE)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"))
    groups = select_marker_images("Sb")
    assert [i["image_id"] for i in groups[0]["images"]] == ["img_seed"]
    assert groups[0]["images"][0]["attached"] is False


def test_an_uploaded_image_is_attached_and_sorts_first():
    _install_catalog(GENE)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"),
                    _entry("img_upload", keys=["Sb"]))
    images = select_marker_images("Sb")[0]["images"]
    assert [i["image_id"] for i in images] == ["img_upload", "img_seed"]
    assert [i["attached"] for i in images] == [True, False]


def test_every_match_is_returned_not_only_the_best():
    _install_catalog(GENE)
    _install_images(_entry("img_a", aliases=["sb"], stem="sb"),
                    _entry("img_b", aliases=["sb"], stem="sb", order=1))
    assert len(select_marker_images("Sb")[0]["images"]) == 2


def test_matches_below_the_floor_land_in_related_not_images():
    """A stem-prefix hit (88) for 'Sb' is any stem starting with 'sb'.

    The marker still has no reference photo worth showing, so `images` holds
    the placeholder card (carrying the upload link) rather than being empty:
    a weak match must not silently satisfy "this marker has an image".
    """
    _install_catalog(GENE)
    _install_images(_entry("img_weak", stem="sbsomethingelse"))
    group = select_marker_images("Sb")[0]
    assert [i["has_image"] for i in group["images"]] == [False]
    assert [i["image_id"] for i in group["related"]] == ["img_weak"]
    assert group["related"][0]["match_score"] < ALIAS_SCORE


def test_rows_carry_every_field_the_shared_macro_reads():
    _install_catalog(GENE)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"))
    row = select_marker_images("Sb")[0]["images"][0]
    for field in ("image_id", "image_url", "has_image", "display_label",
                  "body_part", "effect", "credit", "provenance", "notes",
                  "source_name", "source_url", "source_collection",
                  "marker_key", "match_score", "attached", "origin",
                  "uploaded_by"):
        assert field in row, field
    assert row["image_url"] == "/markers/images/img_seed"
    assert row["marker_key"] == "Sb"
    assert row["effect"] == "short bristles"


def test_a_marker_with_no_images_yields_one_placeholder_row():
    """The shared macro renders a card per row; a marker with nothing must
    still get a card offering the upload link, not vanish."""
    _install_catalog(GENE)
    _install_images(_entry("img_other", aliases=["zz"], stem="zz", body="eye"))
    group = select_marker_images("Sb")[0]
    assert len(group["images"]) == 1
    assert group["images"][0]["has_image"] is False
    assert group["images"][0]["marker_key"] == "Sb"


def test_a_balancer_returns_one_group_per_carried_marker():
    _install_catalog(GENE, BALANCER)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"))
    groups = select_marker_images("CyO")
    assert [g["marker_key"] for g in groups] == ["Sb"]
    assert [i["image_id"] for i in groups[0]["images"]] == ["img_seed"]


def test_an_unknown_key_returns_no_groups():
    _install_catalog(GENE)
    _install_images()
    assert select_marker_images("ghost") == []
