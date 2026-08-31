"""Turning a catalog Key into the marker dict the scorer expects.

The image scorer, the predictor and the viewers all speak in resolved marker
dicts (phenotype_key / display_label / gene_stem / body_part ...). A catalog
Key is not one. Everything in this module exists to close that gap without
each caller reinventing it per kind.
"""
import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.marker_resolution import \
    resolve_definition_markers


def _catalog(*definitions):
    return marker_catalog.compile_catalog(
        {"catalogVersion": 1, "definitions": list(definitions)})


@pytest.fixture(autouse=True)
def _restore_catalog():
    yield
    marker_catalog.reset_catalog()


def _install(*definitions):
    marker_catalog.set_catalog(_catalog(*definitions))


GENE = {"Key": "Sb", "kind": "gene_marker", "match": {"symbol": "Sb"},
        "payload": {"display_label": "Sb", "body_part": "bristle",
                    "effect": "short bristles", "phenotype_key": "Sb"}}
ALLELE = {"Key": "Bl[1]", "kind": "allele_marker",
          "match": {"token": "Bl[1]", "geneStem": "Bl", "alleleSpec": "1"},
          "payload": {"display_label": "Bl", "gene_stem": "Bl",
                      "body_part": "bristle", "phenotype_key": "Bl_1"}}
ALIAS = {"Key": "Gla", "kind": "alias", "match": {"token": "Gla"},
         "payload": {"value": "Sb", "alias_type": "allele_token"}}
BALANCER = {"Key": "CyO", "kind": "balancer",
            "match": {"symbol": "CyO", "aliases": []},
            "payload": {"family": "CyO", "chromosome": 2,
                        "default_markers": ["Sb", "nope"]}}
CONSTRUCT = {"Key": "construct:w+", "kind": "construct_marker",
             "match": {"geneStem": "w", "allelePrefix": "+"},
             "payload": {"overrides": {"display_label": "mini-white",
                                       "phenotype_key": "mini_white",
                                       "body_part": "eye"}}}


def test_a_gene_marker_resolves_to_one_group():
    _install(GENE)
    groups = resolve_definition_markers("Sb")
    assert len(groups) == 1
    assert groups[0]["marker_key"] == "Sb"
    assert groups[0]["display_label"] == "Sb"
    assert groups[0]["marker"]["body_part"] == "bristle"
    assert groups[0]["marker"]["gene_stem"] == "Sb"


def test_an_allele_marker_carries_its_token():
    _install(GENE, ALLELE)
    marker = resolve_definition_markers("Bl[1]")[0]["marker"]
    assert marker["allele_token"] == "Bl[1]"
    assert marker["gene_stem"] == "Bl"


def test_an_alias_resolves_to_its_target():
    _install(GENE, ALIAS)
    groups = resolve_definition_markers("Gla")
    assert groups[0]["marker_key"] == "Sb"
    assert groups[0]["marker"]["display_label"] == "Sb"


def test_a_construct_applies_its_overrides():
    _install(CONSTRUCT)
    marker = resolve_definition_markers("construct:w+")[0]["marker"]
    assert marker["display_label"] == "mini-white"
    assert marker["phenotype_key"] == "mini_white"
    assert marker["body_part"] == "eye"


def test_a_balancer_resolves_to_the_markers_it_carries():
    _install(GENE, BALANCER)
    groups = resolve_definition_markers("CyO")
    assert [g["marker_key"] for g in groups] == ["Sb", "nope"]
    assert groups[0]["marker"]["display_label"] == "Sb"


def test_an_unresolvable_carried_marker_is_an_empty_group_not_a_hole():
    """Dropping it would make a balancer silently under-report what it
    carries, which is worse than showing 'no images for this one'."""
    _install(GENE, BALANCER)
    missing = resolve_definition_markers("CyO")[1]
    assert missing["marker_key"] == "nope"
    assert missing["marker"] is None
    assert missing["display_label"] == "nope"


def test_an_alias_pointing_at_nothing_resolves_to_an_empty_group():
    _install({**ALIAS, "payload": {"value": "ghost"}})
    group = resolve_definition_markers("Gla")[0]
    assert group["marker"] is None
    assert group["marker_key"] == "ghost"


def test_an_alias_chain_is_followed_once_and_then_abandoned():
    """Two aliases pointing at each other must not spin."""
    _install({"Key": "a1", "kind": "alias", "match": {"token": "a1"},
              "payload": {"value": "a2"}},
             {"Key": "a2", "kind": "alias", "match": {"token": "a2"},
              "payload": {"value": "a1"}})
    group = resolve_definition_markers("a1")[0]
    assert group["marker"] is None
    # The key must be the chain's second hop, not the page's own key, or the
    # detail page's unbind form would post this marker against itself.
    assert group["marker_key"] == "a1"


def test_an_unknown_key_resolves_to_nothing():
    _install(GENE)
    assert resolve_definition_markers("ghost") == []


def test_every_shipped_alias_either_resolves_or_is_a_known_exception():
    """Against the real catalog, not a fixture.

    Four of the seven shipped aliases point at a definition Key; `w- -> w[*]`
    resolves only through the gene-stem fallback; the two Orco-LexA spellings
    name a transgene with no marker at all and are expected to stay empty. A
    new alias that silently resolves to nothing should fail here.
    """
    from flymanager.utils.phenotypes.marker_catalog import load_shipped_catalog
    marker_catalog.set_catalog(
        marker_catalog.compile_catalog(load_shipped_catalog()))
    unresolved = set()
    for key, alias in marker_catalog.get_catalog()["aliases"].items():
        groups = resolve_definition_markers(key)
        if not groups or groups[0]["marker"] is None:
            unresolved.add(key)
    assert unresolved == {"OrCo-LexA", "Orco-LexA"}
