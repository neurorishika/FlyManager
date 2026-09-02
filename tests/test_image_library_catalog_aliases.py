import pytest

from flymanager.utils.phenotypes import image_library, marker_catalog


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_shipped_aliases_come_from_the_catalog():
    aliases = image_library._marker_aliases(
        {"phenotype_key": "wg_Gla", "display_label": "Gla"})
    assert "gla" in aliases


def test_construct_marker_aliases_come_from_the_catalog():
    aliases = image_library._marker_aliases(
        {"phenotype_key": "mini_white", "display_label": "mini-white"})
    assert "miniwhite" in aliases
    assert "wplus" in aliases


def test_the_epistasis_key_stays_hardcoded():
    assert "epistasis:w_mini_white_rescue" in image_library.EPISTASIS_IMAGE_ALIASES
    aliases = image_library._marker_aliases(
        {"phenotype_key": "epistasis:w_mini_white_rescue",
         "display_label": "mini-white orange"})
    assert "miniwhite" in aliases


def test_body_part_aliases_come_from_the_data_file():
    """The synonym table left Python for data/markers/body_parts.json; the
    behaviour it drives is locked down in tests/test_body_part_synonyms.py."""
    assert not hasattr(image_library, "BODY_PART_ALIASES")
    assert marker_catalog.get_body_parts()["wing"] == {"wing", "wings"}


def test_a_user_marker_contributes_its_image_aliases():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zz",
        "kind": "gene_marker",
        "match": {"symbol": "zz"},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": "zz",
                    "phenotype_key": "zz", "chromosome": 3,
                    "scoring_confidence": 0.8},
        "sorting": {}, "audit": {},
        "imaging": {"aliases": ["zigzag", "zz-wing"], "images": []},
        "expression": {}, "provenance": {"source": "user"}, "origin": "user",
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))

    aliases = image_library._marker_aliases({"phenotype_key": "zz", "display_label": "zz"})
    assert "zigzag" in aliases
    # _normalize_key maps "-" to the literal word "minus" (pre-existing,
    # unmodified behaviour visible in the shipped mini-white aliases, which
    # list both the hyphenated and unhyphenated spellings explicitly for
    # this reason), so "zz-wing" normalizes to "zzminuswing", not "zzwing".
    assert "zzminuswing" in aliases


def test_legacy_alias_table_is_gone():
    assert not hasattr(image_library, "PHENOTYPE_IMAGE_ALIASES")
