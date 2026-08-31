"""Smaller defects from the branch's adversarial review.

Each is narrow, but each one hides something rather than failing loudly.
"""
import pytest

from flymanager.utils.phenotypes.marker_catalog import compile_catalog


def _catalog_with_balancer(default_markers):
    return compile_catalog({"catalogVersion": 1, "definitions": [
        {"Key": "Sb", "kind": "gene_marker", "match": {"symbol": "Sb"},
         "payload": {"display_label": "Sb", "body_part": "bristle"}},
        {"Key": "TM3", "kind": "balancer", "match": {"symbol": "TM3"},
         "payload": {"default_markers": default_markers, "chromosome": 3}},
    ]})


def test_a_whitespace_padded_carried_marker_still_resolves():
    """`" Sb"` used to vanish from the balancer entirely: the JSON API does not
    strip, and the resolver drops a key that does not match a definition. A
    user sorting on "TM3 carries Sb" was told the class has no Sb."""
    snapshot = _catalog_with_balancer([" Sb", "Sb "])
    assert snapshot["balancer_markers"]["TM3"] == ["Sb", "Sb"]


def test_a_carried_marker_with_no_definition_is_reported_not_swallowed():
    """A typo'd key was silently dropped at resolve time with no unresolved
    token and no warning anywhere."""
    snapshot = _catalog_with_balancer(["Sb", "NotARealMarker"])
    unresolved = snapshot.get("unresolved_balancer_markers") or {}
    assert unresolved.get("TM3") == ["NotARealMarker"]


# The review also flagged the strict getters' truthiness guard on stored
# signatures ("" skips the check). Deliberately NOT changed: caches written
# before signatures existed carry none, and
# test_a_cache_written_before_this_change_has_no_signature_and_is_tolerated
# pins that tolerance as a migration path. Every cache this version writes
# stamps both signatures, so the guard only ever applies to genuinely
# pre-signature documents -- the reviewer called it latent, and it is.
