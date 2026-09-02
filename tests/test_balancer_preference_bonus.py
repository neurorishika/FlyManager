"""Balancer preference moves from DEFAULT_BALANCER_PRIORITY into the catalog.

The bonus was never a ranking: _fallback_priority_score turned a balancer's
index within its chromosome's list into a small additive bonus (0.24 down to
0.06, 0.04 for an unlisted balancer, 0.0 for chromosome 4's empty list) on a
total dominated by breakpoint assessment. The defect was that a lab-added
balancer could never shed the ~0.2 handicap, and could not express a
preference at all.

The seeded values are asserted against the literals the deleted function
returned, so the constant's behaviour is preserved by construction.
"""
import json
import pathlib

import pytest

from flymanager.utils.constraints import balancer_selection
from flymanager.utils.phenotypes import marker_catalog

# What _fallback_priority_score returned for every shipped balancer, captured
# from the function before it was deleted.
SHIPPED_BONUSES = {
    "Basc": 0.04, "Bascy": 0.04, "Binsc": 0.04, "Binscy": 0.04,
    "Binsinscy": 0.04, "CxD": 0.04, "CyO": 0.24, "FM0": 0.04, "FM3": 0.12,
    "FM4": 0.04, "FM6": 0.04, "FM7": 0.16, "FM7a": 0.2, "FM7b": 0.04,
    "FM7c": 0.24, "FM7d": 0.04, "FM7h": 0.04, "FM7i": 0.04, "FM7j": 0.04,
    "FM7k": 0.04, "MKRS": 0.04, "MRS": 0.04, "SM1": 0.12, "SM5": 0.16,
    "SM6a": 0.2, "SM6b": 0.04, "TM1": 0.08, "TM2": 0.12, "TM3": 0.24,
    "TM6": 0.16, "TM6B": 0.2, "TM6C": 0.04, "TM8": 0.04, "TM9": 0.04,
    "TMS": 0.04, "winscy": 0.04,
}


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _shipped_balancers():
    payload = json.loads(
        pathlib.Path("data/markers/catalog.json").read_text(encoding="utf-8"))
    return [d for d in payload["definitions"] if d.get("kind") == "balancer"]


def test_every_shipped_balancer_keeps_the_bonus_it_had():
    seeded = {
        (d.get("match") or {}).get("symbol") or d["Key"]:
            (d.get("sorting") or {}).get("preferenceBonus")
        for d in _shipped_balancers()
    }
    assert seeded == SHIPPED_BONUSES


def test_the_compiled_snapshot_exposes_the_bonuses():
    preference = marker_catalog.get_catalog()["balancer_preference"]
    assert preference["CyO"] == 0.24
    assert preference["TM6B"] == 0.2
    assert preference["SM6b"] == 0.04


def test_the_solver_reads_the_bonus_from_the_catalog():
    assert balancer_selection._preference_bonus("CyO") == 0.24
    assert balancer_selection._preference_bonus("TM3") == 0.24


def test_a_balancer_without_a_bonus_scores_like_an_unlisted_one_did():
    """0.04 was the unlisted-balancer bonus, and stays the default."""
    assert balancer_selection._preference_bonus("no-such-balancer") == 0.04


def test_a_user_added_balancer_can_carry_its_own_preference():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "ZZ1",
        "kind": "balancer",
        "match": {"symbol": "ZZ1"},
        "payload": {"chromosome": 2, "family": "ZZ1", "default_markers": []},
        "sorting": {"preferenceBonus": 0.25},
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))
    assert balancer_selection._preference_bonus("ZZ1") == 0.25


def test_a_bonus_outside_the_allowed_range_is_rejected():
    errors = marker_catalog.validate_definition({
        "Key": "ZZ2", "kind": "balancer", "match": {"symbol": "ZZ2"},
        "payload": {"chromosome": 2},
        "sorting": {"preferenceBonus": 0.9},
    })
    assert any("preferenceBonus" in error for error in errors)


@pytest.mark.parametrize("bonus", ["0.2", True, None])
def test_a_non_numeric_bonus_is_rejected_or_ignored(bonus):
    errors = marker_catalog.validate_definition({
        "Key": "ZZ3", "kind": "balancer", "match": {"symbol": "ZZ3"},
        "payload": {"chromosome": 2},
        "sorting": {"preferenceBonus": bonus},
    })
    if bonus is None:
        assert not any("preferenceBonus" in error for error in errors)
    else:
        assert any("preferenceBonus" in error for error in errors)


def test_the_priority_constant_is_gone():
    from flymanager.utils.constraints import _shared
    assert not hasattr(_shared, "DEFAULT_BALANCER_PRIORITY")
    assert not hasattr(balancer_selection, "_fallback_priority_score")
