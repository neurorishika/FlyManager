# Unified Marker Image Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the marker catalog detail page show the same reference photos the stock and cross viewers already show, by routing it through the one scoring mechanism instead of a dead exact-key index.

**Architecture:** `image_library._score_entry` is already a superset reader — it scores an exact `match.markerKeys` hit at 1000 and falls through to alias/stem/body-part scoring otherwise. The stock and cross viewers use it; the marker page uses `image_catalog`'s `by_marker_key` index, which is empty for all 254 shipped images. We add `select_marker_images(key)` on top of the existing scorer, point the marker page at it, render through the shared `render_phenotype_images` macro, and delete the divergent index. Before any of that we fix the latent defect the design work exposed: the exact tier is unranked and ties resolve by iteration order.

**Tech Stack:** Python 3.10, Flask, Jinja2, MongoDB (pymongo), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-unified-marker-image-resolution-design.md`

## Global Constraints

- **Run tests with the Dockerized Mongo.** From the repo root:
  `export MONGO_URI="mongodb://127.0.0.1:27017/?directConnection=true" MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 SECRET_KEY=test-secret-key MAIL_SUPPRESS_SEND=1`
  then `python -m pytest <target> -q -p no:cacheprovider`. Importing `flymanager.app` connects to Mongo at import time, so the whole suite needs a reachable Mongo. Always `--ignore=tests/new_feature_exploration`.
- **Known pre-existing failures.** `tests/test_phenotype_experiments.py::test_resolve_package_markers_uses_cached_flybase_allele_evidence` and several others in `test_phenotype_backfill`, `test_flybase_admin`, `test_phenotype_routes` fail on `main`. `tests/test_jobs_queue.py` fails at collection. Establish the baseline before blaming your change.
- **Do not change the alias, stem or substring tiers** (98 / 100 / 88 / 72) or their bonuses. `tests/test_marker_image_scoring_parity.py` pins current behaviour across 58 markers and must stay green in every task. Task 1 makes one narrow, argued exception for the exact-key tier only.
- **Do not migrate stored image documents.** An earlier draft proposed backfilling `match.markerKeys` onto the 254 seeded entries; it changes which photo wins for nine markers and was dropped. Nothing in this plan writes to `marker_images`.
- **The score floor is `ALIAS_SCORE` (98).** 88 is the stem-*prefix* tier, not the alias tier.
- **Image metadata must never enter the prediction signature.** It stays in the separate `marker_images` collection. No task in this plan touches a marker definition document.

---

### Task 1: Rank the exact-key tier and make ordering deterministic

The exact tier returns a flat 1000 with none of the bonuses every other tier receives, and `select_phenotype_reference_images` picks a winner with a strict `score > best_score`. Two images that both match a marker by key therefore tie, and the winner is whichever the snapshot sorted first. This is reachable today by uploading two photos for one marker.

**Files:**
- Modify: `flymanager/utils/phenotypes/image_library.py:6-13` (constants), `:83-114` (`_score_entry`), `:186-192` (winner loop)
- Test: `tests/test_marker_image_exact_tier.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: module constants `EXACT_KEY_SCORE = 1000`, `EXACT_STEM_SCORE = 100`, `ALIAS_SCORE = 98`, `STEM_PREFIX_SCORE = 88`, `SUBSTRING_SCORE = 72`; a module function `entry_sort_key(entry, score)` returning `(-score, sortOrder, imageId)` used by Task 3.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_image_exact_tier.py`:

```python
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
        {"entries": [], "by_marker_key": {}, "revision": -1})


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
            {"entries": ordering, "by_marker_key": {}, "revision": 1})
        winners.add(select_phenotype_reference_images([_marker()])[0]["image_id"])
    assert winners == {"img_a"}
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
export MONGO_URI="mongodb://127.0.0.1:27017/?directConnection=true" MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 SECRET_KEY=test-secret-key MAIL_SUPPRESS_SEND=1
python -m pytest tests/test_marker_image_exact_tier.py -q -p no:cacheprovider
```

Expected: collection error — `ImportError: cannot import name 'ALIAS_SCORE'`.

- [ ] **Step 3: Name the tiers**

In `flymanager/utils/phenotypes/image_library.py`, replace the lone `EXACT_KEY_SCORE = 1000` line with:

```python
# Scoring tiers, highest first. Named so a caller can state a floor in terms
# of a tier instead of a magic number -- 88 in particular is the stem-PREFIX
# tier, which reads like an alias tier and is not one.
EXACT_KEY_SCORE = 1000
EXACT_STEM_SCORE = 100
ALIAS_SCORE = 98
STEM_PREFIX_SCORE = 88
SUBSTRING_SCORE = 72
```

Then replace the bare literals inside `_score_entry` with these names: `score = max(score, ALIAS_SCORE)`, `max(score, EXACT_STEM_SCORE)`, `max(score, STEM_PREFIX_SCORE)`, `max(score, SUBSTRING_SCORE)`. Change nothing else about those branches.

- [ ] **Step 4: Give the exact tier its bonuses and add the sort key**

In `_score_entry`, the early return currently reads:

```python
    if entry_keys and any(key in entry_keys for key in _marker_definition_keys(marker)):
        return EXACT_KEY_SCORE
```

Replace it with a jump to the shared bonus block. Restructure the tail of the function so both paths share it:

```python
    if entry_keys and any(key in entry_keys for key in _marker_definition_keys(marker)):
        # An exact binding outranks every fuzzy tier by construction (1000 vs
        # at most 100 + 18 of bonuses), but two exact bindings used to tie at
        # a flat 1000 and resolve by snapshot order. Giving this tier the same
        # bonuses every other tier gets makes "exact AND agrees on body part"
        # beat "exact but does not", which is the honest ranking.
        return _with_bonuses(EXACT_KEY_SCORE, marker, entry)
```

Extract the existing bonus tail into `_with_bonuses`:

```python
def _with_bonuses(score, marker, entry):
    if _body_part_matches(marker, entry):
        score += 8
    if _entry_field(entry, "sourceCollection") == "learning_to_fly":
        score += 4
    if _entry_field(entry, "manifestEntry", False):
        score += 6
    return score + int((entry.get("display") or {}).get("priority", 0) or 0)
```

and end `_score_entry`'s fuzzy path with:

```python
    if score <= 0:
        return 0
    return _with_bonuses(score, marker, entry)
```

**Keep that `if score <= 0: return 0` guard exactly where it is.** Only the
four bonus lines move into `_with_bonuses`. Fold the guard into the helper and
every zero-scoring entry picks up a +8 body-part bonus and starts winning:
`test_marker_image_scoring_parity` and
`test_marker_image_placeholders::test_unmatched_marker_still_gets_a_row` both
go red.

Add the sort key beside it:

```python
def entry_sort_key(entry, score):
    """Total order over scored entries: best first, ties broken explicitly.

    Without the second and third components, equal scores resolve by whatever
    order the snapshot happened to compile in, which differs between processes
    and across deploys.
    """
    display = entry.get("display") or {}
    return (-score, int(display.get("sortOrder") or 0), str(entry.get("imageId") or ""))
```

- [ ] **Step 5: Make the winner loop use it**

In `select_phenotype_reference_images`, replace the accumulate-best loop:

```python
        best_entry, best_score = None, 0
        for entry in entries:
            score = _score_entry(marker, aliases, entry)
            if score > best_score:
                best_entry, best_score = entry, score
```

with:

```python
        scored = [(entry, _score_entry(marker, aliases, entry)) for entry in entries]
        scored = [pair for pair in scored if pair[1] > 0]
        scored.sort(key=lambda pair: entry_sort_key(pair[0], pair[1]))
        best_entry, best_score = scored[0] if scored else (None, 0)
```

- [ ] **Step 6: Run the new test and the parity test together**

```bash
python -m pytest tests/test_marker_image_exact_tier.py tests/test_marker_image_scoring_parity.py tests/test_marker_image_matching.py -q -p no:cacheprovider
```

Expected: all PASS. The parity test must not move — if it does, stop and report which markers changed rather than re-baselining it.

- [ ] **Step 7: Prove no shipped marker has two exact-key matches**

Append to `tests/test_marker_image_exact_tier.py`:

```python
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
```

- [ ] **Step 8: Run it**

```bash
python -m pytest tests/test_marker_image_exact_tier.py -q -p no:cacheprovider
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add tests/test_marker_image_exact_tier.py flymanager/utils/phenotypes/image_library.py
git commit -m "fix: rank the exact-key image tier and break its ties deterministically"
```

---

### Task 2: Resolve a catalog Key to its canonical marker dict

The viewers pass resolved marker dicts produced by the predictor. The marker page has only a definition Key. This task builds the bridge, and it is the whole reason the marker page can use the same scorer.

**Files:**
- Create: `flymanager/utils/phenotypes/marker_resolution.py`
- Test: `tests/test_marker_resolution.py` (create)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `resolve_definition_markers(definition_key)` returning a list of `{"marker_key": str, "display_label": str, "marker": dict | None}` groups. One group for every kind except `balancer`, which returns one per carried marker. `marker` is `None` when the key does not resolve, and callers must render that as an empty group rather than skipping it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_resolution.py`:

```python
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
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
python -m pytest tests/test_marker_resolution.py -q -p no:cacheprovider
```

Expected: `ModuleNotFoundError: No module named 'flymanager.utils.phenotypes.marker_resolution'`.

- [ ] **Step 3: Write the implementation**

Create `flymanager/utils/phenotypes/marker_resolution.py`:

```python
"""Resolve a marker catalog Key into the marker dicts the scorer speaks.

Everything downstream of the predictor -- image scoring especially -- works
on resolved marker dicts (`phenotype_key`, `display_label`, `gene_stem`,
`body_part`, ...). A catalog Key is not one, and each kind reaches its marker
dict differently. Callers that only have a Key, such as the marker catalog
page, would otherwise each reinvent this per kind and drift.

A balancer is deliberately not a marker: it carries markers. Scoring a
balancer directly returns nothing, because its metadata holds none of the
fields the scorer reads, which is why it resolves to one group per carried
marker instead.
"""
import re

from flymanager.utils.phenotypes.marker_catalog import get_catalog
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

# `Orco-LexA -> P{Orco-LexA-VP16}unspecified` has no gene stem to fall back
# to; `w- -> w[*]` does. This is the shape of an allele-ish token.
_STEM_RE = re.compile(r"^([A-Za-z0-9_]+)\[")


def _group(marker_key, marker, fallback_label=None):
    label = ""
    if marker:
        label = str(marker.get("display_label") or "")
    return {"marker_key": marker_key,
            "display_label": label or fallback_label or marker_key,
            "marker": marker}


def _resolve_one(key, catalog, _following_alias=False):
    """One group for a non-balancer Key, or None if the Key is unknown."""
    document = (catalog.get("definitions") or {}).get(key)
    if document is None:
        return None
    kind = document.get("kind")

    if kind in ("gene_marker", "allele_marker"):
        match = document.get("match") or {}
        marker = get_visual_marker(
            match.get("symbol") or match.get("geneStem") or key,
            allele_spec=match.get("alleleSpec"),
            token=key if kind == "allele_marker" else None)
        return _group(key, marker, fallback_label=key)

    if kind == "construct_marker":
        stem = str((document.get("match") or {}).get("geneStem") or "")
        construct = (catalog.get("construct_markers") or {}).get(stem)
        if construct is None:
            return _group(key, None, fallback_label=key)
        marker = dict(get_visual_marker(stem) or {})
        marker.update(construct.get("overrides") or {})
        marker.setdefault("gene_stem", stem)
        return _group(key, marker, fallback_label=key)

    if kind == "alias":
        target = str((document.get("payload") or {}).get("value") or "")
        if _following_alias:
            # An alias chain is a data error, not a feature. Follow one hop so
            # the common `Gla -> wg[Gla-1]` case works, then stop rather than
            # risk a cycle.
            return _group(target or key, None, fallback_label=target or key)
        resolved = _resolve_one(target, catalog, _following_alias=True)
        if resolved is not None:
            return resolved
        # Three shipped aliases point at something that is not a definition
        # Key: `w- -> w[*]` and two Orco-LexA spellings. Fall back to the
        # target's gene stem, which rescues `w[*] -> w` (a real gene marker
        # with white-eye photos) and honestly finds nothing for the transgene.
        stem_match = _STEM_RE.match(target)
        if stem_match:
            marker = get_visual_marker(stem_match.group(1))
            if marker is not None:
                return _group(stem_match.group(1), marker)
        return _group(target or key, None, fallback_label=target or key)

    return _group(key, None, fallback_label=key)


def resolve_definition_markers(definition_key):
    """Groups of resolved markers for a catalog Key.

    One group for every kind except `balancer`, which yields one per carried
    marker. A group whose `marker` is None did not resolve; callers must
    render it as "nothing found" rather than dropping it, or a balancer
    silently under-reports what it carries.
    """
    key = str(definition_key or "").strip()
    catalog = get_catalog()
    document = (catalog.get("definitions") or {}).get(key)
    if document is None:
        return []

    if document.get("kind") == "balancer":
        symbol = str((document.get("match") or {}).get("symbol") or key)
        carried = (catalog.get("balancer_markers") or {}).get(symbol) or []
        groups = []
        for marker_key in carried:
            resolved = _resolve_one(marker_key, catalog)
            groups.append(resolved or _group(marker_key, None,
                                             fallback_label=marker_key))
        return groups

    resolved = _resolve_one(key, catalog)
    return [resolved] if resolved else []
```

- [ ] **Step 4: Run the test and verify it passes**

```bash
python -m pytest tests/test_marker_resolution.py -q -p no:cacheprovider
```

Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_marker_resolution.py flymanager/utils/phenotypes/marker_resolution.py
git commit -m "feat: resolve a marker catalog key into the scorer's marker dicts"
```

---

### Task 3: `select_marker_images` — every image for a Key, grouped

**Files:**
- Modify: `flymanager/utils/phenotypes/image_library.py` (add the function and one shared row builder)
- Test: `tests/test_select_marker_images.py` (create)

**Interfaces:**
- Consumes: `entry_sort_key`, `ALIAS_SCORE` (Task 1); `resolve_definition_markers` (Task 2).
- Produces: `select_marker_images(definition_key, *, min_score=ALIAS_SCORE)` returning a list of groups `{"marker_key", "display_label", "images": [row], "related": [row]}`. Rows carry `image_id`, `image_url`, `has_image`, `display_label`, `body_part`, `effect`, `credit`, `provenance`, `notes`, `source_name`, `source_url`, `source_collection`, `marker_key`, `match_score`, `attached`, `origin`, `uploaded_by`. `attached` is True when the entry's `match.markerKeys` contains this group's `marker_key`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_select_marker_images.py`:

```python
"""Every reference image for a catalog Key, through the shared scorer.

The marker catalog page used an exact-key index that no shipped image is in,
so it showed nothing. This is the replacement, and it deliberately returns
ALL matches rather than the single best one the phenotype views want.
"""
import pytest

from flymanager.utils.phenotypes import image_catalog, marker_catalog
from flymanager.utils.phenotypes.image_library import (ALIAS_SCORE,
                                                       select_marker_images)


@pytest.fixture(autouse=True)
def _restore():
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1})
    marker_catalog.reset_catalog()


GENE = {"Key": "Sb", "kind": "gene_marker", "match": {"symbol": "Sb"},
        "payload": {"display_label": "Sb", "body_part": "bristle",
                    "effect": "short bristles", "phenotype_key": "Sb"}}
BALANCER = {"Key": "CyO", "kind": "balancer", "match": {"symbol": "CyO"},
            "payload": {"default_markers": ["Sb"], "chromosome": 2}}


def _install_catalog(*definitions):
    marker_catalog.set_catalog(marker_catalog.compile_catalog(
        {"catalogVersion": 1, "definitions": list(definitions)}))


def _entry(image_id, *, keys=(), aliases=(), stem="", body="bristle",
           order=0, collection="library"):
    return {"imageId": image_id, "storageId": "s", "sha256": "0" * 64,
            "match": {"markerKeys": list(keys), "aliases": list(aliases),
                      "stem": stem, "bodyPart": body, "manifestEntry": False,
                      "sourceCollection": collection},
            "display": {"sortOrder": order, "priority": 0, "caption": "cap",
                        "credit": "cred", "provenance": "prov",
                        "sourceName": "src"}}


def _install_images(*entries):
    image_catalog.set_image_catalog_for_testing(
        image_catalog.compile_image_catalog(list(entries)))


def test_an_alias_bound_seed_image_is_found_for_its_marker():
    """The regression test for the reported bug: every shipped image has an
    empty markerKeys and was therefore invisible on the marker page."""
    _install_catalog(GENE)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"))
    groups = select_marker_images("Sb")
    assert [i["image_id"] for i in groups[0]["images"]] == ["img_seed"]
    assert groups[0]["images"][0]["attached"] is False


def test_an_uploaded_image_is_attached_and_sorts_first():
    _install_catalog(GENE)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"),
                    _entry("img_upload", keys=["Sb"]))
    images = select_marker_images("Sb")[0]["images"]
    assert [i["image_id"] for i in images] == ["img_upload", "img_seed"]
    assert [i["attached"] for i in images] == [True, False]


def test_every_match_is_returned_not_only_the_best():
    _install_catalog(GENE)
    _install_images(_entry("img_a", aliases=["sb"], stem="sb"),
                    _entry("img_b", aliases=["sb"], stem="sb", order=1))
    assert len(select_marker_images("Sb")[0]["images"]) == 2


def test_matches_below_the_floor_land_in_related_not_images():
    """A stem-prefix hit (88) for 'Sb' is any stem starting with 'sb'.

    The marker still has no reference photo worth showing, so `images` holds
    the placeholder card (carrying the upload link) rather than being empty:
    a weak match must not silently satisfy "this marker has an image".
    """
    _install_catalog(GENE)
    _install_images(_entry("img_weak", stem="sbsomethingelse"))
    group = select_marker_images("Sb")[0]
    assert [i["has_image"] for i in group["images"]] == [False]
    assert [i["image_id"] for i in group["related"]] == ["img_weak"]
    assert group["related"][0]["match_score"] < ALIAS_SCORE


def test_rows_carry_every_field_the_shared_macro_reads():
    _install_catalog(GENE)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"))
    row = select_marker_images("Sb")[0]["images"][0]
    for field in ("image_id", "image_url", "has_image", "display_label",
                  "body_part", "effect", "credit", "provenance", "notes",
                  "source_name", "source_url", "source_collection",
                  "marker_key", "match_score", "attached", "origin",
                  "uploaded_by"):
        assert field in row, field
    assert row["image_url"] == "/markers/images/img_seed"
    assert row["marker_key"] == "Sb"
    assert row["effect"] == "short bristles"


def test_a_marker_with_no_images_yields_one_placeholder_row():
    """The shared macro renders a card per row; a marker with nothing must
    still get a card offering the upload link, not vanish."""
    _install_catalog(GENE)
    _install_images(_entry("img_other", aliases=["zz"], stem="zz", body="eye"))
    group = select_marker_images("Sb")[0]
    assert len(group["images"]) == 1
    assert group["images"][0]["has_image"] is False
    assert group["images"][0]["marker_key"] == "Sb"


def test_a_balancer_returns_one_group_per_carried_marker():
    _install_catalog(GENE, BALANCER)
    _install_images(_entry("img_seed", aliases=["sb"], stem="sb"))
    groups = select_marker_images("CyO")
    assert [g["marker_key"] for g in groups] == ["Sb"]
    assert [i["image_id"] for i in groups[0]["images"]] == ["img_seed"]


def test_an_unknown_key_returns_no_groups():
    _install_catalog(GENE)
    _install_images()
    assert select_marker_images("ghost") == []
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
python -m pytest tests/test_select_marker_images.py -q -p no:cacheprovider
```

Expected: `ImportError: cannot import name 'select_marker_images'`.

- [ ] **Step 3: Extract a shared row builder**

In `image_library.py`, `select_phenotype_reference_images` builds its row dict inline. Extract it so both selectors emit the same shape:

```python
def _image_row(marker, marker_key, entry, score, *, attached=False):
    display = (entry or {}).get("display") or {}
    image_id = entry["imageId"] if entry else None
    return {
        "image_id": image_id,
        "image_url": f"/markers/images/{image_id}" if image_id else None,
        "has_image": entry is not None,
        "marker_key": marker_key,
        "display_label": marker.get("display_label", marker.get("gene_stem", "?")),
        "body_part": marker.get("body_part", ""),
        "effect": marker.get("effect", ""),
        "source_collection": _entry_field(entry, "sourceCollection") if entry else "",
        "source_name": display.get("sourceName", ""),
        "provenance": display.get("provenance", ""),
        "credit": display.get("credit", ""),
        "source_url": display.get("sourceUrl", ""),
        "notes": display.get("caption", ""),
        "match_score": score,
        "attached": attached,
        # The delete gate needs these: delete_marker_image 403s a non-admin
        # for a shipped entry or someone else's upload, so a Remove button
        # rendered without them is a button that only produces a 403.
        "origin": (entry or {}).get("origin", ""),
        "uploaded_by": (entry or {}).get("UploadedBy", ""),
    }
```

Rewrite `select_phenotype_reference_images`'s row append as
`rows.append(_image_row(marker, _definition_key_for(marker), best_entry, best_score))`
and delete the inline dict. Its behaviour and field set are unchanged apart from
the new `attached` key, which is always False there. Note `source_url` in the
list above — `tests/test_marker_image_placeholders.py:118` asserts the exact
field set, so dropping it fails immediately.

- [ ] **Step 4: Add the selector**

```python
def select_marker_images(definition_key, *, min_score=ALIAS_SCORE):
    """Every reference image for a catalog definition, grouped and ranked.

    Unlike select_phenotype_reference_images -- which keeps only the single
    best image per marker, because a phenotype view shows one card per
    predicted marker -- this returns everything that matched, because the
    marker's own page is where you go to see all of them.

    Matches below `min_score` are split into `related` rather than dropped.
    The fuzzy tiers (stem-prefix 88, substring 72) are fine for picking a
    single best image and noisy as a list, but hiding them entirely would
    make an image the app clearly associates with a marker unfindable.
    """
    entries = get_image_catalog()["entries"]
    groups = []
    for resolved in resolve_definition_markers(definition_key):
        marker, marker_key = resolved["marker"], resolved["marker_key"]
        if marker is None:
            groups.append({**{k: resolved[k] for k in ("marker_key", "display_label")},
                           "images": [], "related": []})
            continue

        aliases = _marker_aliases(marker)
        scored = [(entry, _score_entry(marker, aliases, entry)) for entry in entries]
        scored = [pair for pair in scored if pair[1] > 0]
        scored.sort(key=lambda pair: entry_sort_key(pair[0], pair[1]))

        images, related = [], []
        for entry, score in scored:
            attached = marker_key in (_entry_field(entry, "markerKeys", []) or [])
            row = _image_row(marker, marker_key, entry, score, attached=attached)
            (images if score >= min_score else related).append(row)
        if not images:
            # One placeholder row so the shared macro still renders a card
            # carrying the upload link, rather than showing the marker nothing.
            images = [_image_row(marker, marker_key, None, 0)]

        groups.append({"marker_key": marker_key,
                       "display_label": resolved["display_label"],
                       "images": images, "related": related})
    return groups
```

Import `resolve_definition_markers` at module level, beside the existing
`marker_catalog` import at the top of the file. There is no import cycle:
`marker_resolution` imports only `marker_catalog` and `visual_markers`, and
neither imports `image_library`.

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/test_select_marker_images.py tests/test_marker_image_matching.py tests/test_marker_image_placeholders.py tests/test_marker_image_scoring_parity.py -q -p no:cacheprovider
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_select_marker_images.py flymanager/utils/phenotypes/image_library.py
git commit -m "feat: add select_marker_images over the shared image scorer"
```

---

### Task 4: Point the marker page at it and render through the shared macro

**Files:**
- Modify: `flymanager/app/templates/_phenotype_images_macro.html:14` (macro signature and the delete control)
- Modify: `flymanager/app/routes/markers.py:220` (the fix), `:33` (imports)
- Modify: `flymanager/app/templates/markers/detail.html` (replace the bespoke `<figure>` grid)
- Test: `tests/test_marker_detail_images.py` (create)

**Interfaces:**
- Consumes: `select_marker_images` (Task 3).
- Produces: `marker_detail` passes `image_groups` (the Task 3 return value) to the template instead of `images`.

**The Delete gate is the highest-risk part of this plan.** It must mirror
`delete_marker_image`'s own rules or it renders buttons that only produce a
403: that route rejects a non-admin for a `shipped` entry or for someone
else's upload. And note what "Remove" does when the image has exactly one
marker key — it does not unbind, it deletes the document and its GridFS
bytes. That is unchanged from today's page, but today's page could not show a
shipped image at all, so the button now needs a confirm and honest wording.
 `delete_marker_image` (`flymanager/app/routes/markers.py:293`) only *unbinds* when `len(markerKeys) > 1`; otherwise it deletes the document and its bytes, and an admin may do this to a `shipped` entry. Rendering Delete for an image that merely *matched* would put "destroy this shipped photo for every view in the app" one click away on a page where the user believes they are editing one marker. Delete renders only when `attached` is true.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_detail_images.py`:

```python
"""The marker detail page shows what the rest of the app shows.

Every shipped image has an empty match.markerKeys, and this page used to
resolve images through an exact-key index built only from that field, so it
showed nothing for markers whose photos render fine on a stock page.
"""
from unittest.mock import patch

import pytest

from tests.test_marker_routes import (app, fake_db, _client, _reset_catalog,  # noqa: F401
                                      _settings_payload)
from flymanager.utils.phenotypes import image_catalog


@pytest.fixture(autouse=True)
def _freeze_images(monkeypatch):
    """Pin the installed snapshot for the duration of a request.

    `create_app`'s before_request hook calls `maybe_refresh_image_catalog(db)`
    on every request against the real module-global db. The test database has
    254 images at revision 13, so the first client.get() after installing a
    revision-0 test snapshot would recompile from Mongo and throw it away --
    intermittently, since the probe is throttled to 30s. `_reset_catalog` in
    test_marker_routes already does exactly this for the marker catalog.

    Patching the module attribute works because the hook imports the name
    inside the function body, so it resolves at call time.
    """
    monkeypatch.setattr(image_catalog, "maybe_refresh_image_catalog",
                        lambda db, **kwargs: None)
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1})


def _entry(image_id, *, keys=(), aliases=(), stem="", body="wing",
           origin="shipped"):
    return {"imageId": image_id, "storageId": "s", "sha256": "0" * 64,
            "origin": origin, "UploadedBy": "alice",
            "match": {"markerKeys": list(keys), "aliases": list(aliases),
                      "stem": stem, "bodyPart": body, "manifestEntry": False,
                      "sourceCollection": "library"},
            "display": {"sortOrder": 0, "priority": 0}}


def _install(*entries):
    image_catalog.set_image_catalog_for_testing(
        image_catalog.compile_image_catalog(list(entries)))


def test_a_shipped_alias_matched_image_now_appears(app, fake_db):
    """Cy's photos are bound by alias, not by key. This is the bug."""
    _install(_entry("img_seed", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Cy").data.decode()
    assert "/markers/images/img_seed" in body


def test_a_matched_image_gets_no_delete_control(app, fake_db):
    """delete_marker_image destroys an image outright when it has one marker
    key, so offering Delete for something nobody bound here would let one
    click remove a shipped photo from every view in the app."""
    _install(_entry("img_seed", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "admin").get("/markers/Cy").data.decode()
    assert "/markers/images/img_seed" in body
    assert "img_seed/delete" not in body


def test_an_attached_upload_does_get_a_delete_control(app, fake_db):
    _install(_entry("img_mine", keys=["Cy"], stem="cy", origin="user"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/Cy").data.decode()
    assert "img_mine/delete" in body


def test_a_non_admin_gets_no_delete_control_for_someone_elses_upload(app, fake_db):
    """The gate must mirror delete_marker_image, which 403s here. A button
    that can only produce a 403 is worse than no button."""
    _install(_entry("img_theirs", keys=["Cy"], stem="cy", origin="user"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "bob").get("/markers/Cy").data.decode()
    assert "/markers/images/img_theirs" in body
    assert "img_theirs/delete" not in body


def test_a_non_admin_gets_no_delete_control_for_an_attached_shipped_image(app, fake_db):
    """upsert_image_entry binds by content hash, so a user uploading bytes
    identical to a shipped photo attaches their key to the shipped document.
    Attached does not imply deletable."""
    _install(_entry("img_ship", keys=["Cy"], stem="cy", origin="shipped"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/Cy").data.decode()
    assert "img_ship/delete" not in body


def test_the_page_renders_the_shared_macro_not_a_bespoke_grid(app, fake_db):
    _install(_entry("img_seed", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Cy").data.decode()
    assert "stock-phenotype-image-grid" in body
    assert "js-phenotype-image-zoom" in body


def test_a_user_caption_survives_the_move_to_the_shared_macro(app, fake_db):
    """The caption is the only metadata the upload form collects; the old
    bespoke grid rendered it and the shared macro did not."""
    entry = _entry("img_mine", keys=["Cy"], stem="cy", origin="user")
    entry["display"]["caption"] = "scored under the scope at 20x"
    _install(entry)
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/Cy").data.decode()
    assert "scored under the scope at 20x" in body
    assert "Uploaded by alice" in body


def test_a_marker_with_no_images_shows_the_placeholder_card(app, fake_db):
    _install()
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Cy").data.decode()
    assert "stock-phenotype-image-card-empty" in body
    assert "No image yet" in body


def test_a_balancer_page_shows_its_carried_markers(app, fake_db):
    _install(_entry("img_cy", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/CyO").data.decode()
    assert "/markers/images/img_cy" in body
    # CyO carries Cy, pr and cn -- each gets its own labelled section.
    for carried in ("Cy", "pr", "cn"):
        assert f'data-marker-group="{carried}"' in body
```

- [ ] **Step 2: Run the test and verify it fails**

```bash
python -m pytest tests/test_marker_detail_images.py -q -p no:cacheprovider
```

Expected: FAIL — `/markers/images/img_seed` not in body.

- [ ] **Step 3: Teach the macro about deletable images**

In `flymanager/app/templates/_phenotype_images_macro.html`, change the macro signature and add the control. Replace line 14:

```jinja
{% macro render_phenotype_images(images, delete_action=None, csrf_token_value=None, unbind_key=None, viewer=None, viewer_is_admin=false) %}
```

Then inside `stock-phenotype-image-card-body`, immediately before its closing `</div>`, add:

```jinja
            {% if image.notes %}
            <div class="stock-phenotype-image-card-copy mt-1">{{ image.notes }}</div>
            {% endif %}
            {% if image.has_image and image.uploaded_by and image.origin == 'user' %}
            <div class="stock-phenotype-image-card-meta">Uploaded by {{ image.uploaded_by }}</div>
            {% endif %}
            {% set may_delete = viewer_is_admin or (image.origin == 'user' and image.uploaded_by == viewer) %}
            {% if image.attached and delete_action and image.image_id and may_delete %}
            <form method="post" action="{{ url_for(delete_action, image_id=image.image_id) }}" class="mt-2">
                <input type="hidden" name="csrf_token" value="{{ csrf_token_value }}">
                <input type="hidden" name="marker_key" value="{{ unbind_key }}">
                <button type="submit" class="btn btn-sm btn-outline-danger"
                        onclick="return confirm('Remove this image? If it is not attached to any other marker it is deleted for the whole lab.')">Remove</button>
            </form>
            {% endif %}
```

The three existing callers pass no `delete_action`, so nothing renders for them and their output is unchanged.

- [ ] **Step 4: Change the route**

In `flymanager/app/routes/markers.py`, add `select_marker_images` to the `image_library` imports (there is no existing import from that module in this file, so add):

```python
from flymanager.utils.phenotypes.image_library import select_marker_images
```

Replace line 220:

```python
        images=get_image_catalog()["by_marker_key"].get(key, []),
```

with:

```python
        image_groups=select_marker_images(key),
```

- [ ] **Step 5: Rewrite the images panel**

In `flymanager/app/templates/markers/detail.html`, add the macro import beside the existing one at the top:

```jinja
{% import "_phenotype_images_macro.html" as phenotype_images %}
```

Replace `detail.html` **lines 38-60 inclusive** — from `<div class="row">`
through the `</div>` that closes it, which is the line *after* `{% endfor %}`.
Stopping at `{% endfor %}` leaves a stray `</div>` that closes
`.app-form-section` early and throws the upload form out of the panel; no test
catches that, so check the boundary by eye. Leave the upload form beneath it
untouched. Replacement:

```jinja
                {% for group in image_groups %}
                <div class="marker-image-group mb-3" data-marker-group="{{ group.marker_key }}">
                    {% if image_groups|length > 1 %}
                    <h3 class="h6 text-muted">{{ group.display_label }}</h3>
                    {% endif %}
                    {{ phenotype_images.render_phenotype_images(
                        group.images,
                        delete_action='markers.delete_marker_image',
                        csrf_token_value=csrf_token(),
                        unbind_key=group.marker_key,
                        viewer=session.get('username'),
                        viewer_is_admin=is_admin) }}
                    {% if group.related %}
                    <details class="mt-2">
                        <summary class="small text-muted">Possibly related ({{ group.related|length }})</summary>
                        {{ phenotype_images.render_phenotype_images(group.related) }}
                    </details>
                    {% endif %}
                </div>
                {% else %}
                <p class="text-muted">This marker does not resolve to anything the
                    image library knows about.</p>
                {% endfor %}
```

`delete_action` is an endpoint *name*, not a callable — Jinja has no `lambda`.
That is why Step 3's macro builds the URL itself with
`url_for(delete_action, image_id=image.image_id)`.

Then give the page the CSS and JS the macro needs. `detail.html` has no
`page_css` block today; add one directly after its `{% block title %}` line,
matching `stock/view_stock.html:10-14`:

```jinja
{% block page_css %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/phenotype_images.css') }}">
{% endblock %}
```

and add the zoom script as the first line inside the existing
`{% block scripts %}` at the bottom of the file, before the inline script:

```jinja
<script nonce="{{ csp_nonce }}" src="{{ url_for('static', filename='js/marker_image_zoom.js') }}"></script>
```

The zoom overlay itself is already rendered once by `base.html` as a direct
child of `body`, so the page needs nothing else for it to work.

- [ ] **Step 6: Run the tests**

```bash
python -m pytest tests/test_marker_detail_images.py tests/test_marker_routes.py tests/test_marker_form_routes.py -q -p no:cacheprovider
```

Expected: all PASS. `test_marker_routes.py` includes assertions about the old
figure markup — if one fails because the markup legitimately changed, update
that assertion and say so in the commit message.

- [ ] **Step 7: Verify it in the running app**

```bash
./.claude/skills/run-flymanager/driver.sh up
docker compose -f compose.yaml restart app && sleep 8
```

Then screenshot `/markers/Cy`, `/markers/CyO` and `/markers/Sb` using the
pattern in `.claude/skills/run-flymanager/css_audit.py` (`_ensure_auth_state`
for login, `ca.BASE` for the URL). Confirm by eye: Cy shows photos, CyO shows
three labelled sections, no Remove button appears on a shipped image, and the
zoom overlay opens when an image is clicked.

- [ ] **Step 8: Commit**

```bash
git add tests/test_marker_detail_images.py flymanager/app/routes/markers.py \
        flymanager/app/templates/markers/detail.html \
        flymanager/app/templates/_phenotype_images_macro.html
git commit -m "fix: show the whole image library on a marker's own page"
```

---

### Task 5: Delete the divergent index

Only safe once Task 4 has removed the last reader. Doing it earlier breaks the page.

**Files:**
- Modify: `flymanager/utils/phenotypes/image_catalog.py:10` (`_SNAPSHOT` default), `:21-29` (`compile_image_catalog`)
- Modify: `tests/test_marker_image_catalog.py:14`, and the `by_marker_key` literals in `tests/test_marker_image_placeholders.py:37`, `tests/test_marker_image_scoring_parity.py:14`, `tests/test_marker_image_matching.py:12`, `tests/test_marker_image_catalog.py:56`, plus the three new test files from Tasks 1, 3 and 4

**Interfaces:**
- Consumes: nothing. Produces: a snapshot of `{"entries", "revision"}` only.

- [ ] **Step 1: Confirm there are no readers left**

```bash
grep -rn "by_marker_key" flymanager/ | grep -v pycache
```

Expected: only `image_catalog.py` itself. If `routes/markers.py` still appears, Task 4 is incomplete — stop.

Line 220 was the only use of `get_image_catalog` in that file, so also drop it
from the import at `flymanager/app/routes/markers.py:31`, leaving
`bump_image_revision` and `refresh_image_catalog`. Confirm with
`grep -n "get_image_catalog" flymanager/app/routes/markers.py` returning
nothing.

- [ ] **Step 2: Rewrite the one test that pins the index**

In `tests/test_marker_image_catalog.py`, replace `test_compile_indexes_and_totally_orders_entries` with:

```python
def test_compile_totally_orders_entries_and_drops_incomplete_ones():
    """Ordering is still a compile-time guarantee; the by_marker_key index it
    used to feed is gone, because the only consumer that ever read it was
    the marker detail page, and every shipped image has an empty markerKeys
    so that page showed nothing. Images resolve through the scorer now."""
    snapshot = compile_image_catalog([_entry("z", ["Sb"], 0), _entry("a", ["Sb"], 0),
                                      _entry("m", ["Sb"], -1), {"imageId": "broken"}])
    assert [e["imageId"] for e in snapshot["entries"]] == ["m", "a", "z"]
    assert "by_marker_key" not in snapshot
```

- [ ] **Step 3: Run it and verify it fails**

```bash
python -m pytest tests/test_marker_image_catalog.py -q -p no:cacheprovider
```

Expected: FAIL on `assert "by_marker_key" not in snapshot`.

- [ ] **Step 4: Remove the index**

In `flymanager/utils/phenotypes/image_catalog.py`, change the default snapshot at line 10 to `_SNAPSHOT = {"entries": [], "revision": -1}` and rewrite `compile_image_catalog`:

```python
def compile_image_catalog(documents, revision=0):
    entries = [d for d in documents or [] if d.get("imageId") and d.get("storageId")]
    entries.sort(key=_entry_sort_key)
    return {"entries": entries, "revision": int(revision or 0)}
```

- [ ] **Step 5: Update the test literals**

In each of `tests/test_marker_image_placeholders.py:37`, `tests/test_marker_image_scoring_parity.py:14`, `tests/test_marker_image_matching.py:12`, `tests/test_marker_image_catalog.py:56`, and the three test files created in Tasks 1, 3 and 4, change every
`{"entries": [], "by_marker_key": {}, "revision": -1}` to
`{"entries": [], "revision": -1}`. Find them all with:

```bash
grep -rn "by_marker_key" tests/
```

- [ ] **Step 6: Run the full marker suite**

```bash
python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/new_feature_exploration \
    --ignore=tests/test_jobs_queue.py -k "marker or image or phenotype"
```

Expected: PASS except the documented pre-existing failures. Compare against the baseline you established at the start.

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/phenotypes/image_catalog.py tests/
git commit -m "refactor: drop the by_marker_key index now that nothing reads it"
```

---

### Task 6: Close out

- [ ] **Step 1: Run the whole suite against the baseline**

```bash
python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/new_feature_exploration \
    --ignore=tests/test_jobs_queue.py
```

Compare the failure list to the pre-existing baseline listed under Global
Constraints at the top of this plan (there is no baseline file in the repo).
Any failure not on that list is yours.

- [ ] **Step 2: Mark the spec implemented**

Change the `Status:` line of
`docs/superpowers/specs/2026-08-30-unified-marker-image-resolution-design.md`
to `Status: implemented` and note the merge commit.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-08-30-unified-marker-image-resolution-design.md
git commit -m "docs: mark the image resolution spec implemented"
```

---

## Notes for the executor

- **The parity test is a gate, not a formality.** If `test_marker_image_scoring_parity.py` changes at any point, stop and report which markers moved. Do not re-baseline it. An earlier design draft was rejected precisely because it would have quietly changed nine markers' images.
- **Task 4's Delete gate is the one thing worth over-testing.** Getting it wrong destroys shipped images for every user of the app.
- **Follow-up work is queued behind this**, not part of it: typo-proofing the marker forms (tag inputs over catalog vocabulary, duplicate-name checking, upload-on-create). Tagify is already vendored at `flymanager/app/static/vendor/tagify/` with a `clean_tagify_data` helper — use it rather than hand-rolling.
