import pytest

from flymanager.utils.constraints.marker_stability import (
    assess_marker_stability, score_sorting_markers)
from flymanager.utils.phenotypes import marker_catalog


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_shipped_scores_and_notes_are_unchanged():
    assessment = assess_marker_stability("Cy")
    assert assessment["score"] == 0.95
    assert assessment["stability_label"] == "stable"
    assert assessment["notes"] == [
        "Curly wings are treated as the most reliable dominant balancer marker."
    ]


def test_mini_white_label_still_resolves_through_the_marker_dict():
    assessment = assess_marker_stability({"mini_white": True, "gene_stem": "w"})
    assert assessment["marker"] == "mini-white"
    assert assessment["score"] == 0.58


def test_tb_is_downweighted_inside_tm6b_by_the_python_rule():
    assessment = assess_marker_stability("Tb", balancer_symbol="TM6B")
    assert assessment["score"] == 0.35
    assert any("revert" in note for note in assessment["notes"])


def test_unscored_marker_falls_back_to_its_scoring_confidence():
    assessment = assess_marker_stability({"gene_stem": "Sp", "display_label": "Sp",
                                          "token": "wg[Sp-1]"})
    assert assessment["score"] == 0.78


def test_unknown_marker_falls_back_to_the_default():
    assert assess_marker_stability("nothing-at-all")["score"] == 0.68


def test_a_user_defined_score_is_picked_up_without_a_restart():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zz",
        "kind": "gene_marker",
        "match": {"symbol": "zz"},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": "zz",
                    "phenotype_key": "zz", "chromosome": 3,
                    "scoring_confidence": 0.8},
        "sorting": {"stabilityScore": 0.2, "notes": ["User says this is flaky."]},
        "audit": {}, "imaging": {}, "expression": {},
        "provenance": {"source": "user"}, "origin": "user",
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))

    assessment = assess_marker_stability("zz")
    assert assessment["score"] == 0.2
    assert assessment["stability_label"] == "unstable"
    assert assessment["notes"] == ["User says this is flaky."]


def test_score_sorting_markers_still_aggregates():
    summary = score_sorting_markers([{"display_label": "Cy"}, {"display_label": "B"}])
    assert summary["average_score"] == 0.68
    assert summary["weakest_marker"]["marker"] == "B"
