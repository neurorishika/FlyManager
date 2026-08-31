"""Every envelope section must be validated, and compilation must survive anyway.

Definitions arrive through an authenticated HTTP API, and `compile_catalog`
dereferences every section it indexes. A section of the wrong type therefore
raises out of the compile, and compilation has no per-row recovery: the write
has already committed and bumped the revision, so from that moment every
process serves a frozen catalog, every later marker save 500s in the
post-write refresh, and nothing points at the offending row because it never
reaches `invalid_definitions`.

Hardening one section is not hardening the class -- `sorting` was fixed first
and these five variants survived it.
"""
import pytest

from flymanager.utils.phenotypes.marker_catalog import (compile_catalog,
                                                        validate_definition)


def _gene(**overrides):
    document = {"Key": "zz", "kind": "gene_marker", "match": {"symbol": "zz"},
                "payload": {"display_label": "zz"}}
    document.update(overrides)
    return document


def _compile(*documents):
    return compile_catalog({"catalogVersion": 1, "definitions": []}, list(documents))


@pytest.mark.parametrize("document", [
    _gene(imaging="oops"),
    _gene(audit="oops"),
    _gene(provenance="oops"),
    _gene(imaging={"aliases": "not-a-list"}),
    _gene(imaging={"aliases": [{"nested": 1}]}),
    {"Key": "b", "kind": "balancer", "match": {"symbol": "b", "aliases": 123}, "payload": {}},
    {"Key": "b", "kind": "balancer", "match": {"symbol": "b", "aliases": "CyO2"}, "payload": {}},
    {"Key": "b", "kind": "balancer", "match": {"symbol": "b"},
     "payload": {"default_markers": [{"k": 1}]}},
])
def test_a_malformed_section_is_reported_and_never_raises(document):
    assert validate_definition(document), "should not have validated"
    snapshot = _compile(document)
    assert snapshot["definitions"] == {}
    assert [entry["Key"] for entry in snapshot["invalid_definitions"]] == [document["Key"]]


def test_string_aliases_do_not_become_per_character_balancer_symbols():
    """`aliases: "CyO2"` used to compile cleanly and register C, y, O and 2 as
    balancer symbols the genotype parser matches -- silently wrong predictions
    carrying a perfectly valid catalog signature."""
    snapshot = _compile({"Key": "CyO", "kind": "balancer",
                         "match": {"symbol": "CyO", "aliases": "CyO2"}, "payload": {}})
    assert snapshot["balancer_aliases"] == {}
    assert "CyO" not in snapshot["definitions"]


def test_one_bad_row_never_takes_the_good_rows_with_it():
    good = _gene(Key="good")
    snapshot = _compile(good, _gene(Key="bad", imaging="oops"))
    assert "good" in snapshot["definitions"]
    assert [e["Key"] for e in snapshot["invalid_definitions"]] == ["bad"]


def test_an_unexpected_shape_is_contained_rather_than_raising():
    """Belt and braces: validation cannot enumerate every future field, so the
    index loop itself must route a raise to invalid_definitions instead of
    letting it escape and freeze the catalog."""
    class Hostile(dict):
        def get(self, key, default=None):
            if key == "sorting":
                raise RuntimeError("boom")
            return super().get(key, default)

    document = Hostile(_gene(Key="hostile"))
    snapshot = _compile(document, _gene(Key="fine"))
    assert "fine" in snapshot["definitions"]
    assert "hostile" not in snapshot["definitions"]


@pytest.mark.parametrize("document", [
    _gene(),
    _gene(imaging={"aliases": ["w+", "wplus"], "images": []}),
    _gene(audit={"isProbeMarker": True, "probeSymbol": "w"}),
    _gene(provenance={"source": "user", "geneName": "white"}),
    {"Key": "b", "kind": "balancer",
     "match": {"symbol": "b", "aliases": ["b2"]},
     "payload": {"default_markers": ["Sb"], "notes": []}},
])
def test_well_formed_definitions_still_validate_and_compile(document):
    assert validate_definition(document) == []
    assert document["Key"] in _compile(document)["definitions"]


def test_the_shipped_catalog_is_unaffected():
    from flymanager.utils.phenotypes.marker_catalog import load_shipped_catalog
    snapshot = compile_catalog(load_shipped_catalog())
    assert len(snapshot["definitions"]) == 104
    assert snapshot["invalid_definitions"] == []
