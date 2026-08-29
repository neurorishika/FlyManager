import pytest

from flymanager.utils.phenotypes import construct_markers
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.construct_markers import (
    construct_marker_pattern, extract_construct_marker_symbols,
    extract_construct_markers)


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_mini_white_override_is_unchanged():
    markers = extract_construct_markers("P{UAS-GFP}attP40, w[+mC]")
    assert len(markers) == 1
    marker = markers[0]
    assert marker["display_label"] == "mini-white"
    assert marker["phenotype_key"] == "mini_white"
    assert marker["mini_white"] is True
    assert marker["dominance"] == "dominant"
    assert marker["allele_spec"] == "+mC"
    assert marker["source"] == "construct_marker"
    assert marker["construct_type"] == "P"


def test_y_and_v_overrides_are_unchanged():
    y_marker = extract_construct_markers("P{x}y[+t7.7]")[0]
    assert (y_marker["display_label"], y_marker["phenotype_key"]) == ("y+", "y_plus")
    v_marker = extract_construct_markers("P{x}v[+t1.8]")[0]
    assert (v_marker["display_label"], v_marker["body_part"]) == ("v+", "eye")


def test_symbols_are_deduplicated():
    assert extract_construct_marker_symbols("P{x}w[+mC] w[+mC]") == \
        [{"gene_stem": "w", "allele_spec": "+mC"}]


def test_a_user_defined_construct_marker_is_matched_and_applied():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [
        {
            "Key": "ry",
            "kind": "gene_marker",
            "match": {"symbol": "ry"},
            "payload": {"body_part": "eye", "effect": "rosy eyes",
                        "dominance": "recessive", "display_label": "ry",
                        "phenotype_key": "ry", "chromosome": 3,
                        "scoring_confidence": 0.8},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
        {
            "Key": "construct:ry+",
            "kind": "construct_marker",
            "match": {"geneStem": "ry", "allelePrefix": "+"},
            "payload": {"overrides": {"dominance": "dominant",
                                      "display_label": "ry+",
                                      "effect": "rosy rescue marker",
                                      "phenotype_key": "ry_plus"}},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
    ]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))

    markers = extract_construct_markers("P{x}ry[+t7.2]")
    assert [marker["display_label"] for marker in markers] == ["ry+"]
    assert markers[0]["phenotype_key"] == "ry_plus"


def test_a_stem_with_no_base_marker_is_skipped():
    assert extract_construct_markers("P{x}q[+]") == []


def test_equal_length_stems_get_a_deterministic_alternation_order(monkeypatch):
    """construct_marker_pattern sorts stems longest-first so a longer stem is
    never shadowed by a prefix of it, but used to break ties on `entries`'
    (a dict's) iteration order, which is not guaranteed stable across a
    catalog rebuild -- the same class of bug already fixed for
    marker_catalog.balancer_match_order. Same-length stems must come out in
    a fixed order (alphabetical) regardless of how they were inserted."""
    monkeypatch.setattr(
        construct_markers, "get_catalog",
        lambda: {"construct_markers": {"zz": {}, "aa": {}, "mm": {}}})

    pattern = construct_marker_pattern()

    assert pattern.pattern == r"(?P<stem>aa|mm|zz)\[(?P<allele>\+[^\]]*)\]"
