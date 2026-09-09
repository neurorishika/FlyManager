"""The list fields become tag inputs instead of comma-separated text.

"Sb, Ser" typed into a textarea is one stray keystroke away from "Sb Ser",
which is a single marker named "Sb Ser" that resolves to nothing. Tags make
each value a discrete object the person can see, and Tagify is configured to
write plain comma-separated text back into the original input, so the server
parses exactly what it parsed before and the field degrades to today's
textarea with JavaScript off.
"""
import pytest

from flymanager.utils.phenotypes.marker_fields import (MARKER_FIELD_SPECS,
                                                       form_to_document,
                                                       iter_fields)


def _field(kind, path):
    return next(f for f in iter_fields(kind) if f["path"] == path)


@pytest.mark.parametrize("kind,path", [
    ("balancer", "payload.default_markers"),
    ("balancer", "match.aliases"),
    ("gene_marker", "imaging.aliases"),
])
def test_the_short_list_fields_are_tag_inputs(kind, path):
    assert _field(kind, path)["tags"] is True


def test_the_contextual_stability_balancers_are_a_tag_input():
    group = next(f for f in iter_fields("gene_marker") if f["repeating"])
    balancers = next(f for f in group["fields"] if f["path"] == "whenBalancer")
    assert balancers["tags"] is True


def test_notes_stay_a_plain_textarea():
    """A balancer note is a sentence -- "breaks down at 25C, use fresh" -- and
    is split on newlines alone precisely so a comma cannot cut it in half."""
    notes = _field("balancer", "payload.notes")
    assert notes["tags"] is False
    assert notes["separator"] == "\n"


def test_no_newline_separated_list_is_ever_a_tag_input():
    for kind in MARKER_FIELD_SPECS:
        for field in iter_fields(kind):
            for entry in [field] + list(field.get("fields") or ()):
                if entry.get("tags"):
                    assert entry["separator"] == ","


def test_what_tagify_posts_parses_to_what_the_textarea_posted():
    """Tagify writes back plain comma-separated text, so this is the same
    string either way -- the point of configuring it that way."""
    typed = form_to_document("balancer", {
        "match.symbol": "TM3", "payload.default_markers": "Sb, Ser"})
    tagified = form_to_document("balancer", {
        "match.symbol": "TM3", "payload.default_markers": "Sb,Ser"})
    assert typed["payload"]["default_markers"] == ["Sb", "Ser"]
    assert tagified["payload"]["default_markers"] == ["Sb", "Ser"]


def test_a_single_tag_with_no_comma_still_parses():
    document = form_to_document("balancer", {
        "match.symbol": "TM3", "payload.default_markers": "Sb"})
    assert document["payload"]["default_markers"] == ["Sb"]


def test_clearing_every_tag_empties_the_list():
    document = form_to_document("balancer", {
        "match.symbol": "TM3", "payload.default_markers": ""})
    assert document["payload"]["default_markers"] == []
