"""`sorting` must be validated, not trusted.

compile_catalog turns `sorting.stabilityScore` into a float. Any definition
that reaches compilation with a non-numeric score therefore raises out of the
compile, and compilation has no per-row recovery -- one bad row takes down
every catalog read for every user. Since any logged-in user can POST a
definition through the marker API, that row is reachable from outside.

The catalog's stated contract is the opposite: a bad row is skipped and
recorded in invalid_definitions. These tests pin that contract for `sorting`.
"""
import pytest

from flymanager.utils.phenotypes.marker_catalog import (compile_catalog,
                                                        validate_definition)


def _definition(sorting):
    return {"Key": "zz", "kind": "gene_marker", "match": {"symbol": "zz"},
            "payload": {"display_label": "zz"}, "sorting": sorting}


def _compile(document):
    return compile_catalog({"catalogVersion": 1, "definitions": []}, [document])


@pytest.mark.parametrize("bad", [
    {"stabilityScore": "banana"},
    {"stabilityScore": None, "notes": "not a list"},
    {"stabilityScore": [0.5]},
    {"stabilityScore": {"value": 0.5}},
])
def test_a_malformed_sorting_section_is_reported_not_raised(bad):
    assert validate_definition(_definition(bad)), f"{bad} should not validate"
    snapshot = _compile(_definition(bad))
    assert "zz" not in snapshot["definitions"]
    assert [e["Key"] for e in snapshot["invalid_definitions"]] == ["zz"]


def test_a_non_object_sorting_section_is_reported():
    assert validate_definition(_definition("nope"))


@pytest.mark.parametrize("good", [
    {}, {"notes": []}, {"stabilityScore": 0.45, "notes": []},
    {"stabilityScore": 1}, {"stabilityScore": 0},
])
def test_well_formed_sorting_sections_still_validate(good):
    assert validate_definition(_definition(good)) == []
    assert "zz" in _compile(_definition(good))["definitions"]


def test_a_score_outside_zero_to_one_is_rejected():
    """stabilityScore is consumed as a 0..1 confidence by marker_stability's
    stable/moderate/unstable banding; a score of 40 would read as 'stable'
    while meaning nothing."""
    assert validate_definition(_definition({"stabilityScore": 40}))
    assert validate_definition(_definition({"stabilityScore": -1}))


def test_a_boolean_is_not_a_valid_score():
    """bool is an int subclass in Python, so a naive isinstance check lets
    True through as 1.0."""
    assert validate_definition(_definition({"stabilityScore": True}))
