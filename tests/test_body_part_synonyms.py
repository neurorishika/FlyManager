"""Body-part synonyms move from a Python constant to data/markers/body_parts.json.

The pairs below are the behaviour of the hardcoded BODY_PART_ALIASES table as
it stood before the move, asserted first so the move cannot quietly change
image matching. The asymmetric entries are the interesting ones: "antenna"
lists "head" but "head" does not list "antenna", so antenna->head matches and
head->antenna does not, and that asymmetry is deliberate.
"""
import json

import pytest

from flymanager.utils.phenotypes import image_library, marker_catalog


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_body_parts()
    yield
    marker_catalog.reset_body_parts()


def _matches(marker_part, entry_part):
    return image_library._body_part_matches(
        {"body_part": marker_part}, {"match": {"bodyPart": entry_part}})


@pytest.mark.parametrize("marker_part,entry_part,expected", [
    ("wing", "wings", True),
    ("wings", "wing", True),
    ("eye", "eyes", True),
    ("bristle", "bristles", True),
    ("haltere", "halteres", True),
    ("thorax", "shoulder", True),
    ("shoulder", "thorax", True),
    ("antenna", "head", True),
    ("head", "antenna", False),
    ("body", "body", True),
    ("wing", "eye", False),
    ("wing", "", False),
    ("", "wing", False),
    ("Wing", "WINGS", True),
    ("notch", "notch", True),
    ("notch", "wing", False),
])
def test_body_part_matching_is_unchanged(marker_part, entry_part, expected):
    assert _matches(marker_part, entry_part) is expected


def test_the_shipped_file_carries_the_table_that_used_to_be_hardcoded():
    table = marker_catalog.get_body_parts()
    assert table["wing"] == {"wing", "wings"}
    assert table["antenna"] == {"antenna", "head"}
    assert table["head"] == {"head"}


def test_a_malformed_file_fails_loudly(tmp_path):
    path = tmp_path / "body_parts.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        marker_catalog.load_body_parts(path)


@pytest.mark.parametrize("payload", [
    [],
    {"bodyParts": []},
    {"bodyParts": {"wing": "wings"}},
    {"bodyParts": {"wing": ["wings", 3]}},
    {"bodyParts": {"wing": []}},
    {"bodyParts": {"": ["wing"]}},
])
def test_a_structurally_wrong_file_fails_loudly(tmp_path, payload):
    path = tmp_path / "body_parts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        marker_catalog.load_body_parts(path)


def test_a_missing_file_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="Unable to read"):
        marker_catalog.load_body_parts(tmp_path / "nope.json")


def test_the_catalog_signature_is_untouched_by_this_slice():
    """K3 moves no definition, so every cached prediction stays valid.

    The literal is the shipped catalog's signature. K3 did not change it
    (it was 37fec1358889aecc13f86e7f97bae4ea before and after); K1 and K2
    then did, by seeding sorting.preferenceBonus on every balancer and
    sorting.contextualStability on Tb, which is exactly what the field
    changes are supposed to do. Updating this literal is how that change
    gets noticed rather than absorbed.
    """
    snapshot = marker_catalog.compile_catalog(
        marker_catalog.load_shipped_catalog(), [])
    assert marker_catalog.compute_marker_catalog_signature(snapshot) == \
        "2b7897af5078d7477857c81d07943635"
    assert "bodyParts" not in snapshot
