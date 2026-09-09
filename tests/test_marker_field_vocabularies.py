"""The form's suggestion lists are derived from the catalog, never duplicated.

A marker definition edited through these pages goes live for the whole lab
immediately, and the people qualified to correct one know flies, not schemas.
Every field whose vocabulary already exists therefore offers it, and offers it
from the catalog itself, so a marker added yesterday is suggested today with
no list maintained anywhere.
"""
import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.marker_fields import (MARKER_FIELD_SPECS,
                                                       form_to_document,
                                                       iter_fields,
                                                       marker_vocabularies)


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _field(kind, path):
    return next(f for f in iter_fields(kind) if f["path"] == path)


def test_body_parts_offer_the_canonical_keys_only():
    """The synonym table exists so an image filename saying "wings" matches a
    marker saying "wing". What a marker stores should be the canonical one."""
    body_parts = marker_vocabularies()["body_parts"]
    assert "wing" in body_parts
    assert "wings" not in body_parts
    assert "shoulder" not in body_parts
    assert body_parts == sorted(marker_catalog.get_body_parts())


def test_sources_come_from_definitions_already_in_the_catalog():
    sources = marker_vocabularies()["sources"]
    assert "manual_dictionary" in sources
    assert sources == sorted(set(sources))


def test_families_come_from_the_balancers():
    families = marker_vocabularies()["families"]
    assert "CyO" in families and "TM3" in families


def test_definition_keys_and_balancer_symbols_are_offered():
    vocabularies = marker_vocabularies()
    assert "Sb" in vocabularies["definition_keys"]
    assert "TM6B" in vocabularies["balancer_symbols"]
    assert "Sb" not in vocabularies["balancer_symbols"]


def test_gene_stems_include_both_allele_and_construct_stems():
    stems = marker_vocabularies()["gene_stems"]
    assert "Bl" in stems     # allele marker payload stem
    assert "w" in stems      # construct marker match stem


def test_a_user_added_marker_joins_the_vocabulary_with_no_list_edited():
    shipped = marker_catalog.load_shipped_catalog()
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, [{
        "Key": "zzvocab",
        "kind": "balancer",
        "match": {"symbol": "zzvocab"},
        "payload": {"chromosome": 2, "family": "ZZ", "default_markers": []},
        "provenance": {"source": "the_lab_notebook"},
    }]))
    vocabularies = marker_vocabularies()
    assert "ZZ" in vocabularies["families"]
    assert "the_lab_notebook" in vocabularies["sources"]
    assert "zzvocab" in vocabularies["balancer_symbols"]


def test_every_named_vocabulary_actually_exists():
    """A field naming a vocabulary nobody provides would render an empty list
    that looks like "there are no valid values" rather than "unconstrained"."""
    available = set(marker_vocabularies())
    for kind in MARKER_FIELD_SPECS:
        for field in iter_fields(kind):
            named = [field] + list(field.get("fields") or ())
            for entry in named:
                if entry.get("vocabulary"):
                    assert entry["vocabulary"] in available, entry["path"]


@pytest.mark.parametrize("kind,path,vocabulary", [
    ("gene_marker", "payload.body_part", "body_parts"),
    ("gene_marker", "provenance.source", "sources"),
    ("allele_marker", "payload.gene_stem", "gene_stems"),
    ("allele_marker", "match.geneStem", "gene_stems"),
    ("alias", "payload.value", "definition_keys"),
    ("balancer", "payload.family", "families"),
    ("balancer", "payload.default_markers", "definition_keys"),
    ("construct_marker", "match.geneStem", "gene_stems"),
    ("construct_marker", "payload.overrides.body_part", "body_parts"),
])
def test_the_fields_that_have_a_vocabulary_name_it(kind, path, vocabulary):
    assert _field(kind, path)["vocabulary"] == vocabulary


def test_the_contextual_stability_balancers_are_offered_too():
    group = next(f for f in iter_fields("gene_marker") if f["repeating"])
    balancers = next(f for f in group["fields"] if f["path"] == "whenBalancer")
    assert balancers["vocabulary"] == "balancer_symbols"


def test_chromosome_is_a_select_that_names_the_x():
    field = _field("gene_marker", "payload.chromosome")
    assert field["type"] == "select"
    assert field["options"] == (("1", "1 (X)"), ("2", "2"), ("3", "3"), ("4", "4"))


def test_a_chosen_chromosome_still_posts_as_an_int():
    document = form_to_document("gene_marker", {
        "match.symbol": "zz", "payload.chromosome": "3"})
    assert document["payload"]["chromosome"] == 3


def test_a_blank_chromosome_stays_absent():
    """The gene-marker help says to leave it blank when it varies."""
    document = form_to_document("gene_marker", {
        "match.symbol": "zz", "payload.chromosome": ""})
    assert "chromosome" not in document["payload"]


def test_the_balancer_chromosome_posts_as_an_int_too():
    """Balancer candidates are filtered by an int chromosome, so a string
    here would quietly make a balancer a candidate for nothing."""
    document = form_to_document("balancer", {
        "match.symbol": "zz", "payload.chromosome": "2"})
    assert document["payload"]["chromosome"] == 2

