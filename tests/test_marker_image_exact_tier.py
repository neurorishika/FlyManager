"""The exact-key tier must be ranked and its ties broken deterministically.

_score_entry returned a flat EXACT_KEY_SCORE with none of the bonuses the
alias tiers get, and the winner loop used a strict `>`, so two images bound
to the same marker tied and the winner was whichever the snapshot happened
to sort first. That is reachable today by uploading two photos for one
marker.
"""
import pytest

from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_library import (
    ALIAS_SCORE, EXACT_KEY_SCORE, STEM_PREFIX_SCORE, SUBSTRING_SCORE,
    _score_entry, entry_sort_key, select_phenotype_reference_images)


@pytest.fixture(autouse=True)
def _restore_snapshot():
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "revision": -1})


def _entry(image_id, *, keys=(), stem="", body="wing", order=0, priority=0):
    return {"imageId": image_id, "storageId": "s", "sha256": "0" * 64,
            "match": {"markerKeys": list(keys), "aliases": [], "stem": stem,
                      "bodyPart": body, "manifestEntry": False,
                      "sourceCollection": ""},
            "display": {"sortOrder": order, "priority": priority}}


def _marker():
    return {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"}


def test_tier_constants_match_their_tiers():
    assert (EXACT_KEY_SCORE, ALIAS_SCORE, STEM_PREFIX_SCORE, SUBSTRING_SCORE) \
        == (1000, 98, 88, 72)


def test_an_exact_match_agreeing_on_body_part_outranks_one_that_does_not():
    marker, aliases = _marker(), set()
    agrees = _score_entry(marker, aliases, _entry("a", keys=["Sb[1]"], body="wing"))
    differs = _score_entry(marker, aliases, _entry("b", keys=["Sb[1]"], body="eye"))
    assert agrees > differs >= EXACT_KEY_SCORE


def test_an_exact_match_still_outranks_every_alias_tier_match():
    """Bonuses must not let a fuzzy match overtake an exact one."""
    marker, aliases = _marker(), {"sb"}
    exact = _score_entry(marker, aliases, _entry("a", keys=["Sb[1]"], body="eye"))
    fuzzy = _score_entry(marker, aliases, _entry("b", stem="sb", body="wing",
                                                 priority=9))
    assert exact > fuzzy


def test_tied_exact_matches_resolve_by_sort_order_then_image_id():
    entries = [_entry("img_b", keys=["Sb[1]"], order=1),
               _entry("img_a", keys=["Sb[1]"], order=1),
               _entry("img_z", keys=["Sb[1]"], order=0)]
    ordered = sorted(entries, key=lambda e: entry_sort_key(e, EXACT_KEY_SCORE))
    assert [e["imageId"] for e in ordered] == ["img_z", "img_a", "img_b"]


def test_the_winner_does_not_depend_on_snapshot_order():
    entries = [_entry("img_a", keys=["Sb[1]"], order=0),
               _entry("img_b", keys=["Sb[1]"], order=0)]
    winners = set()
    for ordering in (entries, list(reversed(entries))):
        image_catalog.set_image_catalog_for_testing(
            {"entries": ordering, "revision": 1})
        winners.add(select_phenotype_reference_images([_marker()])[0]["image_id"])
    assert winners == {"img_a"}


def test_no_shipped_marker_has_two_exact_key_matches():
    """The precondition that makes Task 1 a no-op on real data.

    Re-ranking the exact tier cannot change any current result while every
    marker has at most one exact-key match. When that stops being true this
    assertion fails, rather than the behaviour drifting silently.
    """
    import json
    from flymanager.utils.phenotypes.image_seed import DEFAULT_SEED_DIR
    from flymanager.utils.phenotypes.marker_catalog import (compile_catalog,
                                                            load_shipped_catalog)
    # DEFAULT_SEED_DIR rather than a relative path: pytest may be invoked from
    # anywhere, and a cwd-relative read would pass or fail on that alone.
    seed = json.loads(
        (DEFAULT_SEED_DIR / "index.json").read_text(encoding="utf-8"))
    catalog = compile_catalog(load_shipped_catalog())
    counts = {}
    for record in seed:
        for key in (record.get("match") or {}).get("markerKeys") or []:
            counts[key] = counts.get(key, 0) + 1
    # Vacuous today -- every shipped entry has an empty markerKeys, so
    # `counts` is empty. That is the point: it stays a canary for the day
    # somebody starts binding seed images by key.
    assert {k: v for k, v in counts.items() if v > 1} == {}
    assert set(counts) <= set(catalog["definitions"])
