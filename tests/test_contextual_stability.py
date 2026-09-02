"""The Tb/TM6B stability rule moves from an `if` into the catalog.

It was the only context-dependent stability rule in the codebase -- the score
depends on which balancer carries the marker -- which is why it did not fit
the flat stability table and got hardcoded on two magic strings instead.
"""
import pytest

from flymanager.utils.constraints.marker_stability import (
    assess_marker_stability)
from flymanager.utils.phenotypes import marker_catalog

REVERSION_NOTE = ("TM6B explicitly keeps Tb because the marker can revert at "
                  "high frequency.")


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


@pytest.mark.parametrize("balancer", ["TM6B", "TM6"])
def test_tb_on_a_tm6_balancer_is_capped_as_before(balancer):
    assessment = assess_marker_stability("Tb", balancer_symbol=balancer)
    assert assessment["score"] == 0.35
    assert assessment["stability_label"] == "unstable"


def test_tb_emits_both_notes_exactly_as_it_did():
    """The shipped Tb definition already carries a reversion note in
    sorting.notes, so a capped Tb emits two near-identical notes. That is
    today's behaviour; asserting it makes a later "cleanup" fail loudly
    instead of quietly changing output."""
    notes = assess_marker_stability("Tb", balancer_symbol="TM6B")["notes"]
    assert notes == [
        "Tubby is down-weighted because TM6B/Tb reversion is a known high-frequency risk.",
        REVERSION_NOTE,
    ]


def test_tb_on_another_balancer_keeps_its_base_score():
    assessment = assess_marker_stability("Tb", balancer_symbol="TM3")
    assert assessment["score"] == 0.5
    assert REVERSION_NOTE not in assessment["notes"]


def test_tb_with_no_balancer_keeps_its_base_score():
    assert assess_marker_stability("Tb")["score"] == 0.5


def test_a_marker_with_no_contextual_rule_is_unaffected():
    assert assess_marker_stability("Sb", balancer_symbol="TM6B") == \
        assess_marker_stability("Sb")


def test_when_balancer_matches_the_raw_symbol_not_an_alias():
    """Bug-compatible with the deleted `if`, deliberately. Canonicalizing
    through balancer_aliases would be its own change with its own test."""
    shipped = marker_catalog.load_shipped_catalog()
    for definition in shipped["definitions"]:
        if definition.get("Key") == "TM6B":
            definition["match"] = dict(definition.get("match") or {})
            definition["match"]["aliases"] = ["TM6-B"]
            break
    else:
        raise AssertionError("the shipped catalog no longer defines TM6B")
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, []))
    assert marker_catalog.get_catalog()["balancer_aliases"]["TM6-B"] == "TM6B"
    assert assess_marker_stability("Tb", balancer_symbol="TM6-B")["score"] == 0.5


def test_a_cap_applies_to_a_definition_with_no_base_score():
    """The reason contextual rules are indexed outside the stability table."""
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zzcap",
        "kind": "gene_marker",
        "match": {"symbol": "zzcap"},
        "payload": {"display_label": "zzcap", "effect": "x",
                    "dominance": "dominant", "body_part": "wing",
                    "phenotype_key": "zzcap", "chromosome": 2},
        "sorting": {"contextualStability": [
            {"whenBalancer": ["CyO"], "maxScore": 0.1, "note": "capped"}]},
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))
    assert "zzcap" not in marker_catalog.get_catalog()["stability"]
    capped = assess_marker_stability("zzcap", balancer_symbol="CyO")
    assert capped["score"] == 0.1
    assert capped["notes"] == ["capped"]


def test_rules_apply_in_declaration_order_and_each_caps():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zztwo",
        "kind": "gene_marker",
        "match": {"symbol": "zztwo"},
        "payload": {"display_label": "zztwo", "effect": "x",
                    "dominance": "dominant", "body_part": "wing",
                    "phenotype_key": "zztwo", "chromosome": 2},
        "sorting": {"stabilityScore": 0.9, "contextualStability": [
            {"whenBalancer": ["CyO"], "maxScore": 0.6, "note": "first"},
            {"whenBalancer": ["CyO"], "maxScore": 0.3, "note": "second"}]},
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))
    capped = assess_marker_stability("zztwo", balancer_symbol="CyO")
    assert capped["score"] == 0.3
    assert capped["notes"] == ["first", "second"]


def test_a_cap_never_raises_a_score():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zzlow",
        "kind": "gene_marker",
        "match": {"symbol": "zzlow"},
        "payload": {"display_label": "zzlow", "effect": "x",
                    "dominance": "dominant", "body_part": "wing",
                    "phenotype_key": "zzlow", "chromosome": 2},
        "sorting": {"stabilityScore": 0.2, "contextualStability": [
            {"whenBalancer": ["CyO"], "maxScore": 0.8, "note": ""}]},
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))
    assert assess_marker_stability("zzlow", balancer_symbol="CyO")["score"] == 0.2


@pytest.mark.parametrize("contextual,expected", [
    ("not a list", "must be a list"),
    (["not an object"], "must be an object"),
    ([{"whenBalancer": "TM6B", "maxScore": 0.3}], "whenBalancer"),
    ([{"whenBalancer": [3], "maxScore": 0.3}], "whenBalancer"),
    ([{"whenBalancer": ["TM6B"]}], "maxScore is required"),
    ([{"whenBalancer": ["TM6B"], "maxScore": True}], "maxScore must be a number"),
    ([{"whenBalancer": ["TM6B"], "maxScore": "0.3"}], "maxScore must be a number"),
    ([{"whenBalancer": ["TM6B"], "maxScore": 1.5}], "between 0 and 1"),
    ([{"whenBalancer": ["TM6B"], "maxScore": 0.3, "note": 7}], "note must be a string"),
])
def test_a_malformed_rule_is_rejected(contextual, expected):
    errors = marker_catalog.validate_definition({
        "Key": "zzbad", "kind": "gene_marker", "match": {"symbol": "zzbad"},
        "payload": {"display_label": "zzbad", "effect": "x",
                    "dominance": "dominant", "body_part": "wing",
                    "phenotype_key": "zzbad", "chromosome": 2},
        "sorting": {"contextualStability": contextual},
    })
    assert any(expected in error for error in errors), errors


def test_the_hardcoded_rule_is_gone():
    import inspect

    from flymanager.utils.constraints import marker_stability
    source = inspect.getsource(marker_stability.assess_marker_stability)
    assert 'label == "Tb"' not in source
    assert "0.35" not in source
