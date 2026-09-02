"""The contextual stability rules are editable through the marker form.

This is the form's first repeating group -- every other field is one input at
one dotted path -- so the round trip is worth pinning down: what renders, what
comes back, and what a blank row means.
"""
import pytest

from flymanager.utils.phenotypes.marker_fields import (MARKER_FIELD_SPECS,
                                                       form_to_document,
                                                       iter_fields,
                                                       repeating_input_name,
                                                       repeating_rows)

FIELD = next(f for f in iter_fields("gene_marker") if f["repeating"])


def _form(rows):
    form = {"match.symbol": "Tb", "payload.display_label": "Tb"}
    for index, row in enumerate(rows):
        for name, value in row.items():
            form[f"sorting.contextualStability.{index}.{name}"] = value
    return form


def test_the_group_is_registered_on_the_kinds_whose_stability_is_scored():
    for kind in ("gene_marker", "allele_marker"):
        assert any(f["repeating"] for f in iter_fields(kind))


def test_stored_rules_render_with_spare_blank_rows():
    definition = {"sorting": {"contextualStability": [
        {"whenBalancer": ["TM6B"], "maxScore": 0.35, "note": "reverts"}]}}
    rows = repeating_rows(definition, FIELD)
    assert rows[0]["maxScore"] == 0.35
    assert rows[1:] == [{}, {}]


def test_a_marker_with_no_rules_still_offers_blank_rows():
    assert repeating_rows({}, FIELD) == [{}, {}]


def test_input_names_carry_the_row_index():
    subfield = FIELD["fields"][0]
    assert repeating_input_name(FIELD, 2, subfield) == \
        "sorting.contextualStability.2.whenBalancer"


def test_a_submitted_row_becomes_a_rule():
    document = form_to_document("gene_marker", _form([
        {"whenBalancer": "TM6B, TM6", "maxScore": "0.35", "note": "reverts"}]))
    assert document["sorting"]["contextualStability"] == [
        {"whenBalancer": ["TM6B", "TM6"], "maxScore": 0.35, "note": "reverts"}]


def test_blank_rows_are_dropped():
    document = form_to_document("gene_marker", _form([
        {"whenBalancer": "TM6B", "maxScore": "0.35", "note": ""},
        {"whenBalancer": "", "maxScore": "", "note": ""},
    ]))
    assert document["sorting"]["contextualStability"] == [
        {"whenBalancer": ["TM6B"], "maxScore": 0.35}]


def test_clearing_every_row_removes_the_rules():
    existing = {"sorting": {"contextualStability": [
        {"whenBalancer": ["TM6B"], "maxScore": 0.35}]}}
    document = form_to_document(
        "gene_marker", _form([{"whenBalancer": "", "maxScore": "", "note": ""}]),
        existing=existing)
    assert "contextualStability" not in document["sorting"]


def test_a_gap_in_the_row_indices_does_not_shift_later_rows():
    """Indices come from the submitted names, not from a count."""
    form = {"sorting.contextualStability.0.whenBalancer": "TM6B",
            "sorting.contextualStability.0.maxScore": "0.35",
            "sorting.contextualStability.7.whenBalancer": "CyO",
            "sorting.contextualStability.7.maxScore": "0.5"}
    document = form_to_document("gene_marker", form)
    assert [row["whenBalancer"] for row in document["sorting"]["contextualStability"]] == \
        [["TM6B"], ["CyO"]]


def test_a_non_numeric_cap_is_reported_to_the_user():
    with pytest.raises(ValueError, match="must be a number"):
        form_to_document("gene_marker", _form([
            {"whenBalancer": "TM6B", "maxScore": "loads", "note": ""}]))


def test_other_sorting_keys_survive_a_save():
    """The form covers contextualStability; it must not drop a sibling."""
    existing = {"sorting": {"stabilityScore": 0.5, "notes": ["keep me"]}}
    document = form_to_document("gene_marker", _form([
        {"whenBalancer": "TM6B", "maxScore": "0.35", "note": ""}]), existing=existing)
    assert document["sorting"]["stabilityScore"] == 0.5
    assert document["sorting"]["notes"] == ["keep me"]


def test_the_balancer_preference_field_allows_its_real_range():
    field = next(f for f in iter_fields("balancer")
                 if f["path"] == "sorting.preferenceBonus")
    assert (field["minimum"], field["maximum"]) == (0, 0.25)


def test_every_other_field_keeps_the_default_range():
    field = next(f for f in iter_fields("gene_marker")
                 if f["path"] == "payload.scoring_confidence")
    assert (field["minimum"], field["maximum"]) == (0, 1)


def test_unrendered_sorting_keys_survive_a_save_with_no_rules():
    """The sorting section is rebuilt now, so it must carry over like the rest."""
    existing = {"sorting": {"stabilityScore": 0.5, "someAdminOnlyKey": 7}}
    document = form_to_document(
        "gene_marker", {"match.symbol": "Sb"}, existing=existing)
    assert document["sorting"] == {"stabilityScore": 0.5, "someAdminOnlyKey": 7}
