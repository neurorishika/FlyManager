"""Structured (non-JSON) marker form fields.

The catalog UI used to edit every envelope section as a raw JSON textarea,
which only made sense to someone who already knew the document shape. These
tests pin the field spec and the form parser that replaced it: the parser must
produce exactly the same envelope a hand-written JSON textarea produced, and
must never drop a key the form did not render.
"""
import pytest
from werkzeug.datastructures import MultiDict

from flymanager.utils.phenotypes.marker_fields import (
    MARKER_FIELD_SPECS, field_value, form_to_document)
from flymanager.utils.phenotypes.marker_catalog import MARKER_KINDS


def test_every_kind_has_a_field_spec():
    assert set(MARKER_FIELD_SPECS) == set(MARKER_KINDS)


def test_gene_marker_form_round_trips_to_the_catalog_envelope():
    form = MultiDict({
        "match.symbol": "Sb",
        "payload.display_label": "Sb",
        "payload.phenotype_key": "Sb",
        "payload.effect": "short stubble bristles",
        "payload.body_part": "bristle",
        "payload.chromosome": "3",
        "payload.dominance": "dominant",
        "payload.scoring_confidence": "0.8",
        "payload.homozygous_lethal": "on",
        "provenance.source": "user",
    })
    document = form_to_document("gene_marker", form)
    assert document["match"] == {"symbol": "Sb"}
    assert document["payload"] == {
        "display_label": "Sb", "phenotype_key": "Sb",
        "effect": "short stubble bristles", "body_part": "bristle",
        "chromosome": 3, "dominance": "dominant",
        "scoring_confidence": 0.8, "homozygous_lethal": True,
    }
    assert document["provenance"] == {"source": "user"}


def test_unchecked_checkbox_is_false_not_missing():
    form = MultiDict({"match.symbol": "Sb", "payload.homozygous_lethal__present": "1"})
    document = form_to_document("gene_marker", form)
    assert document["payload"]["homozygous_lethal"] is False


def test_blank_text_fields_are_omitted_rather_than_stored_empty():
    document = form_to_document("gene_marker", MultiDict({
        "match.symbol": "Sb", "payload.display_label": "", "payload.effect": "  ",
    }))
    assert "display_label" not in document["payload"]
    assert "effect" not in document["payload"]


def test_list_fields_split_on_commas_and_newlines():
    document = form_to_document("balancer", MultiDict({
        "match.symbol": "TM3",
        "payload.default_markers": "Sb,  Ser\nUbx",
        "payload.notes": "",
    }))
    assert document["payload"]["default_markers"] == ["Sb", "Ser", "Ubx"]
    assert document["payload"]["notes"] == []


def test_construct_marker_nests_overrides():
    document = form_to_document("construct_marker", MultiDict({
        "match.geneStem": "y", "match.allelePrefix": "+",
        "payload.overrides.display_label": "y+",
        "payload.overrides.effect": "yellow rescue marker",
    }))
    assert document["payload"] == {"overrides": {
        "display_label": "y+", "effect": "yellow rescue marker"}}


def test_alias_keeps_its_two_fields():
    document = form_to_document("alias", MultiDict({
        "match.token": "wplus", "payload.value": "w+",
        "payload.alias_type": "construct_token",
    }))
    assert document["match"] == {"token": "wplus"}
    assert document["payload"] == {"value": "w+", "alias_type": "construct_token"}


def test_keys_the_form_never_rendered_survive_a_save():
    """A structured form only renders known fields; an unknown key added
    through the admin JSON editor must not be wiped by a later form save."""
    existing = {
        "match": {"symbol": "Sb", "customMatcher": "keep-me"},
        "payload": {"display_label": "Sb", "experimental_flag": 7},
        "imaging": {"aliases": ["Sb"], "images": []},
    }
    document = form_to_document("gene_marker", MultiDict({
        "match.symbol": "Sb", "payload.display_label": "Stubble",
    }), existing=existing)
    assert document["match"] == {"symbol": "Sb", "customMatcher": "keep-me"}
    assert document["payload"] == {"display_label": "Stubble", "experimental_flag": 7}
    # imaging.aliases is a rendered field, but imaging.images -- which the form
    # never shows, because image metadata is stored separately -- must survive.
    assert document["imaging"]["images"] == []
    # Sections no field of this kind touches are left to the route's merge.
    assert "expression" not in document and "sorting" not in document


def test_cleared_field_is_removed_even_when_it_exists_today():
    document = form_to_document("gene_marker", MultiDict({
        "match.symbol": "Sb", "payload.display_label": "",
    }), existing={"payload": {"display_label": "Sb", "effect": "old"}})
    assert document["payload"] == {}


def test_invalid_number_is_a_clear_error_not_a_traceback():
    with pytest.raises(ValueError) as excinfo:
        form_to_document("gene_marker", MultiDict({
            "match.symbol": "Sb", "payload.chromosome": "banana"}))
    assert "Chromosome" in str(excinfo.value)


def test_field_value_reads_nested_paths_for_prefilling():
    document = {"payload": {"overrides": {"display_label": "y+"}},
                "match": {"geneStem": "y"}}
    assert field_value(document, "payload.overrides.display_label") == "y+"
    assert field_value(document, "match.geneStem") == "y"
    assert field_value(document, "payload.missing") == ""


def test_field_value_renders_lists_as_comma_separated_text():
    document = {"payload": {"default_markers": ["Sb", "Ser"]}}
    assert field_value(document, "payload.default_markers") == "Sb, Ser"


def test_a_note_containing_a_comma_stays_one_note():
    """Balancer notes are free text; splitting them on commas would quietly
    turn 'breaks down at 25C, use fresh' into two notes on the next save."""
    document = form_to_document("balancer", MultiDict({
        "match.symbol": "TM3",
        "payload.notes": "breaks down at 25C, use fresh\nkeep at 18C",
    }))
    assert document["payload"]["notes"] == ["breaks down at 25C, use fresh",
                                            "keep at 18C"]
    assert field_value(document, "payload.notes", "\n") == (
        "breaks down at 25C, use fresh\nkeep at 18C")
