# Provider Match Cache Unification & Apply-to-Record Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Run this plan after:** `docs/superpowers/plans/2026-08-14-mongodb-efficiency.md`. That plan's Task 1 creates `tests/mongo_fakes.py` (`FakeDatabase`/`FakeCollection` with `find_one_and_update`, `delete_many`, `distinct`, `$or`-query support) — Task 3 below imports it directly. This plan also modifies two files the efficiency plan touches (`flymanager/app/routes/stock.py`, `flymanager/app/jobs/tasks.py`); running the efficiency plan first avoids merge churn. Do not start this plan until `tests/mongo_fakes.py` exists on the branch.

**Goal:** Migrate `ProviderMatchCache` onto the shared `materialized_cache.py` backfill engine (fixing a real staleness gap where FlyBase reference-data updates never invalidated the cache), and add a generic, confirm-before-overwrite "apply a cached provider match into the stock record" action — the one thing none of the app's three materialized caches currently support.

**Architecture:** No new services or collections. `materialized_cache.py` gains per-record error containment so a single bad candidate lookup can't abort a bulk run. `ProviderMatchCache`'s envelope gains `version`/`pipelineSignature` fields, matching the pattern `PhenotypeCache`/`StandardizationCache` already use, while its existing public helpers (`_get_valid_provider_match_cache`, `_store_provider_match_cache`) keep their current signatures and return shapes so no caller or existing test needs to change. A new pure diff helper in `mongo_records.py` computes what an "apply" would change and flags conflicts; the actual write goes through the existing `edit_stock` function (which already does the `ModificationLog`/`DataModifiedDate` bookkeeping via `apply_updates_to_owned_document`) — no new write path is invented.

**Tech Stack:** Flask, PyMongo, pytest, `tests/mongo_fakes.py` (from the prerequisite plan) and the existing `_make_app(monkeypatch)` Flask-test-client fixture pattern already used in `tests/test_stock_sources.py`.

**Spec:** `docs/superpowers/specs/2026-08-14-provider-match-cache-unification-design.md`

## Global Constraints

- Every fix must be behavior-preserving except where this plan explicitly documents a shape change (the new `errors` key in `backfill_materialized_cache`'s summary dict, and the new `version`/`pipelineSignature` fields in `ProviderMatchCache`). A legacy-shaped `ProviderMatchCache` entry (missing those fields) simply fails the new validity check once and gets rebuilt on next access — no migration script needed.
- No new dependencies.
- `apply_provider_match` never trusts client-supplied field values — it always re-derives the field mapping from the server-held cached candidate by index.
- `ProviderMatchCache` is stock-only (verified: no cross equivalent exists). This plan does not touch `flymanager/app/routes/cross.py`.
- Standardization's existing client-side-only `applyStandardizationReplacement` flow (`view_stock.html:1116-1131`) is untouched — this plan does not generalize it.

---

## Task 1: `materialized_cache.py` — contain per-record builder errors

**Files:**
- Modify: `flymanager/utils/materialized_cache.py:59-97`
- Modify: `tests/test_phenotype_backfill.py:90,119,149` (update 3 exact-dict summary assertions)
- Modify: `tests/test_standardization_cache.py:137,154,170` (update 3 exact-dict summary assertions)
- Test: `tests/test_materialized_cache_errors.py`

**Interfaces:**
- Consumes: `tests/mongo_fakes.py` `FakeDatabase` (from the prerequisite plan).
- Produces: `backfill_materialized_cache(...)` — same signature; returned summary dict gains a fourth key, `"errors"` (int, count of records whose `cache_builder` raised). `cache_builder` exceptions no longer propagate — they're caught, logged via the existing behavior of continuing the loop, and the record is left unmodified (no `$set`). Later tasks (2 and 3) rely on this: a bad Bloomington/FlyBase lookup for one stock must not abort the bulk provider-match refresh.

Today, `cache_builder(record)` is only ever called at `$set` time (never during `dry_run`), so an exception there currently crashes `backfill_materialized_cache`'s whole loop. This task wraps that one call in `try/except` and adds the `errors` counter, without changing `dry_run`'s existing behavior (it still doesn't call `cache_builder` at all).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_materialized_cache_errors.py
import flymanager.app  # noqa: F401  (see tests/test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.materialized_cache import backfill_materialized_cache


def test_backfill_materialized_cache_counts_and_skips_a_raising_record():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Cache": None},
            {"UniqueID": "s2", "User": "alice", "Cache": None},
        ]
    })
    collection = db["stocks"]

    def builder(record):
        if record["UniqueID"] == "s1":
            raise RuntimeError("boom")
        return {"value": "ok"}

    summary = backfill_materialized_cache(
        collection,
        cache_field="Cache",
        cache_getter=lambda record: record.get("Cache"),
        cache_builder=builder,
        query={},
    )

    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 0, "errors": 1}
    assert collection.find_one({"UniqueID": "s1"})["Cache"] is None
    assert collection.find_one({"UniqueID": "s2"})["Cache"] == {"value": "ok"}


def test_backfill_materialized_cache_dry_run_still_never_calls_builder():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice", "Cache": None}]})
    collection = db["stocks"]
    calls = []

    def builder(record):
        calls.append(record["UniqueID"])
        return {"value": "ok"}

    summary = backfill_materialized_cache(
        collection,
        cache_field="Cache",
        cache_getter=lambda record: record.get("Cache"),
        cache_builder=builder,
        query={},
        dry_run=True,
    )

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
    assert calls == []
    assert collection.find_one({"UniqueID": "s1"})["Cache"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_materialized_cache_errors.py -v`
Expected: FAIL on `test_backfill_materialized_cache_counts_and_skips_a_raising_record` — `RuntimeError: boom` propagates uncaught, and/or the summary dict has no `"errors"` key.

- [ ] **Step 3: Implement the error containment**

```python
# flymanager/utils/materialized_cache.py — replace backfill_materialized_cache (lines 59-97)
def backfill_materialized_cache(collection, *, cache_field, cache_getter, cache_builder,
                                query=None, projection=None, users=None,
                                cache_selector_builder=_default_selector,
                                dry_run=False, force=False):
    """Recompute a materialized cache field across a collection.

    Skips records whose cache is already valid (``cache_getter`` returns a
    truthy value) unless ``force`` is set. Honours a ``users`` maintainer filter
    both at the query level and per-record. A ``cache_builder`` exception is
    caught, counted in ``errors``, and that record is left unmodified rather
    than aborting the run. Returns a summary dict with ``scanned`` /
    ``updated`` / ``skipped_valid`` / ``errors`` counts.
    """
    if query is None:
        query = build_user_query(users)

    summary = {
        "scanned": 0,
        "updated": 0,
        "skipped_valid": 0,
        "errors": 0,
    }

    for record in collection.find(query, projection):
        if not record_matches_users(record, users):
            continue
        summary["scanned"] += 1

        if not force and cache_getter(record):
            summary["skipped_valid"] += 1
            continue

        if dry_run:
            summary["updated"] += 1
            continue

        try:
            cache_payload = cache_builder(record)
        except Exception:
            summary["errors"] += 1
            continue

        summary["updated"] += 1
        collection.update_one(
            cache_selector_builder(record),
            {"$set": {cache_field: cache_payload}},
        )

    return summary
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `python -m pytest tests/test_materialized_cache_errors.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Update the two existing test files whose summary assertions now need the `errors` key**

The returned summary dict gained a key, so exact-dict-equality assertions in the phenotype/standardization backfill tests must be updated. Their builders never raise, so this is purely a shape update — `errors` is always `0` there.

```python
# tests/test_phenotype_backfill.py — update the three summary assertions
# line 90:
    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 1, "errors": 0}
# line 119:
    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
# line 149:
    assert summary == {"scanned": 2, "updated": 2, "skipped_valid": 0, "errors": 0}
```

```python
# tests/test_standardization_cache.py — update the three summary assertions
# line 137:
    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 1, "errors": 0}
# line 154:
    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
# line 170:
    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
```

- [ ] **Step 6: Run the full affected test suite to confirm no regressions**

Run: `python -m pytest tests/test_materialized_cache_errors.py tests/test_phenotype_backfill.py tests/test_standardization_cache.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/materialized_cache.py tests/test_materialized_cache_errors.py tests/test_phenotype_backfill.py tests/test_standardization_cache.py
git commit -m "perf: contain per-record cache_builder errors in backfill_materialized_cache"
```

---

## Task 2: Versioned envelope + pipeline-signature validity for `ProviderMatchCache`

**Files:**
- Modify: `flymanager/app/routes/stock.py` (imports; new constant/helpers near line 51; `_get_valid_provider_match_cache`/`_store_provider_match_cache` bodies, lines 481-521)
- Test: `tests/test_stock_sources.py` (new tests, appended near the existing provider-match tests around line 1198)

**Interfaces:**
- Consumes: `compute_flybase_pipeline_signature()` (`flymanager/utils/phenotypes/flybase_pipeline.py:271`).
- Produces:
  - `PROVIDER_MATCH_CACHE_VERSION = 1` (module constant).
  - `_is_provider_match_cache_entry_valid(cache_payload, stock, source_context) -> bool` — the single source of truth for cache validity, used by both the on-demand path (this task) and the bulk backfill wrapper (Task 3).
  - `_build_provider_match_cache_envelope(stock, source_context, candidates) -> dict` — builds the full `{version, pipelineSignature, signature, candidates, count, cachedAt}` envelope (does not write to the database).
  - `_get_valid_provider_match_cache(stock, source_context)` and `_store_provider_match_cache(stock, source_context, candidates)` — **unchanged signatures and return shapes** (both still return the display-shaped payload from `_build_provider_match_payload`), so every existing caller and every existing test that patches these two functions by name keeps working untouched. Only their internal validity check / stored envelope changes.

This closes a real gap: today `_get_valid_provider_match_cache` only checks a per-record `signature` (`_build_provider_match_cache_signature`, unchanged by this task) — it never notices when FlyBase reference data has been re-ingested with new candidate stocks. Adding `pipelineSignature` makes it behave like `PhenotypeCache`/`StandardizationCache` already do.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stock_sources.py` (same file already testing provider-match helpers; reuses its existing `_make_app` fixture pattern for the import to succeed):

```python
def test_is_provider_match_cache_entry_valid_checks_version_pipeline_and_signature(monkeypatch):
    from flymanager.app.routes.stock import (
        PROVIDER_MATCH_CACHE_VERSION, _build_provider_match_cache_signature,
        _is_provider_match_cache_entry_valid)

    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig-1",
    )

    stock = {
        "StockSource": "BDSC", "SourceID": "17", "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
    }
    source_context = {
        "sourceType": "BDSC", "sourceCollection": "Bloomington",
        "flyBaseStockID": "FBst0000017", "providerURL": "",
    }
    record_signature = _build_provider_match_cache_signature(stock, source_context)

    valid_cache = {
        "version": PROVIDER_MATCH_CACHE_VERSION,
        "pipelineSignature": "pipeline-sig-1",
        "signature": record_signature,
        "candidates": [],
        "cachedAt": "2026-04-17 10:00",
    }

    assert _is_provider_match_cache_entry_valid(valid_cache, stock, source_context) is True
    assert _is_provider_match_cache_entry_valid(None, stock, source_context) is False
    assert _is_provider_match_cache_entry_valid(
        {**valid_cache, "version": PROVIDER_MATCH_CACHE_VERSION - 1}, stock, source_context
    ) is False
    assert _is_provider_match_cache_entry_valid(
        {**valid_cache, "pipelineSignature": "stale-pipeline-sig"}, stock, source_context
    ) is False
    assert _is_provider_match_cache_entry_valid(
        {**valid_cache, "signature": "wrong-signature"}, stock, source_context
    ) is False
    assert _is_provider_match_cache_entry_valid(
        {**valid_cache, "candidates": "not-a-list"}, stock, source_context
    ) is False
    assert _is_provider_match_cache_entry_valid(
        {**valid_cache, "cachedAt": "not-a-timestamp"}, stock, source_context
    ) is False


def test_build_provider_match_cache_envelope_stamps_version_and_pipeline_signature(monkeypatch):
    from flymanager.app.routes.stock import (PROVIDER_MATCH_CACHE_VERSION,
                                              _build_provider_match_cache_envelope)

    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig-2",
    )

    stock = {"StockSource": "BDSC", "SourceID": "17", "FlyBaseStockID": "FBst0000017", "Genotype": "w[*]"}
    source_context = {
        "sourceType": "BDSC", "sourceCollection": "Bloomington",
        "flyBaseStockID": "FBst0000017", "providerURL": "",
    }
    candidates = [{"stockSource": "VIENNA", "sourceID": "4321"}]

    envelope = _build_provider_match_cache_envelope(stock, source_context, candidates)

    assert envelope["version"] == PROVIDER_MATCH_CACHE_VERSION
    assert envelope["pipelineSignature"] == "pipeline-sig-2"
    assert envelope["candidates"] == candidates
    assert envelope["count"] == 1
    assert envelope["cachedAt"]


def test_get_valid_provider_match_cache_rejects_legacy_entry_missing_version(monkeypatch):
    from flymanager.app.routes.stock import (_build_provider_match_cache_signature,
                                              _get_valid_provider_match_cache)

    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig-3",
    )

    stock_fields = {
        "StockSource": "BDSC", "SourceID": "17", "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]",
    }
    source_context = {
        "sourceType": "BDSC", "sourceCollection": "Bloomington",
        "flyBaseStockID": "FBst0000017", "providerURL": "",
    }
    stock = dict(stock_fields)
    stock["ProviderMatchCache"] = {
        # Legacy shape: no "version", no "pipelineSignature" — must be treated as stale.
        "signature": _build_provider_match_cache_signature(stock_fields, source_context),
        "candidates": [],
        "cachedAt": "2026-04-17 10:00",
    }

    assert _get_valid_provider_match_cache(stock, source_context) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_stock_sources.py -k "provider_match_cache_entry or cache_envelope or legacy_entry_missing_version" -v`
Expected: FAIL — `ImportError`/`AttributeError` for `PROVIDER_MATCH_CACHE_VERSION`, `_is_provider_match_cache_entry_valid`, `_build_provider_match_cache_envelope` (none exist yet).

- [ ] **Step 3: Add the import and the new constant/helpers**

```python
# flymanager/app/routes/stock.py — add to the existing import block (near the other
# flymanager.utils.phenotypes.predictor import)
from flymanager.utils.phenotypes.flybase_pipeline import \
    compute_flybase_pipeline_signature
```

```python
# flymanager/app/routes/stock.py — replace line 51
PROVIDER_MATCH_CACHE_FIELD = "ProviderMatchCache"
PROVIDER_MATCH_CACHE_VERSION = 1
```

```python
# flymanager/app/routes/stock.py — add above _get_valid_provider_match_cache (was line 481)
def _is_provider_match_cache_entry_valid(cache_payload, stock, source_context):
    if not isinstance(cache_payload, dict):
        return False
    if cache_payload.get("version") != PROVIDER_MATCH_CACHE_VERSION:
        return False
    if str(cache_payload.get("pipelineSignature", "")) != compute_flybase_pipeline_signature():
        return False
    if not isinstance(cache_payload.get("candidates"), list):
        return False
    if _parse_provider_match_cache_timestamp(str(cache_payload.get("cachedAt") or "")) is None:
        return False
    if cache_payload.get("signature") != _build_provider_match_cache_signature(stock, source_context):
        return False
    return True


def _build_provider_match_cache_envelope(stock, source_context, candidates):
    return {
        "version": PROVIDER_MATCH_CACHE_VERSION,
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "signature": _build_provider_match_cache_signature(stock, source_context),
        "candidates": candidates,
        "count": len(candidates),
        "cachedAt": current_timestamp(),
    }
```

- [ ] **Step 4: Rewrite `_get_valid_provider_match_cache` and `_store_provider_match_cache` to use the new helpers, without changing their signatures or return shapes**

```python
# flymanager/app/routes/stock.py — replace _get_valid_provider_match_cache (lines 481-502)
def _get_valid_provider_match_cache(stock, source_context):
    cache_payload = stock.get(PROVIDER_MATCH_CACHE_FIELD)
    if not _is_provider_match_cache_entry_valid(cache_payload, stock, source_context):
        return None

    return _build_provider_match_payload(
        cache_payload.get("candidates") or [],
        cached=True,
        cached_at=str(cache_payload.get("cachedAt") or ""),
    )


# flymanager/app/routes/stock.py — replace _store_provider_match_cache (lines 505-521)
def _store_provider_match_cache(stock, source_context, candidates):
    cache_payload = _build_provider_match_cache_envelope(stock, source_context, candidates)
    db["stocks"].update_one(
        {"UniqueID": stock["UniqueID"], "User": stock["User"]},
        {"$set": {PROVIDER_MATCH_CACHE_FIELD: cache_payload}},
    )
    return _build_provider_match_payload(
        candidates,
        cached=False,
        cached_at=cache_payload["cachedAt"],
    )
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_stock_sources.py -k "provider_match_cache_entry or cache_envelope or legacy_entry_missing_version" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full existing provider-match test suite to confirm no regressions (unchanged signatures should keep every existing patch-based test green)**

Run: `python -m pytest tests/test_stock_sources.py -v`
Expected: PASS. Note: `test_admin_can_refresh_provider_match_cache_for_all_stocks` was already failing before this plan (it patches `flymanager.app.routes.settings._run_provider_match_cache_refresh`, an attribute that does not exist anywhere in the codebase — confirmed by `grep -rn "_run_provider_match_cache_refresh" flymanager/ tests/` returning only that one test reference). This is a pre-existing, unrelated broken test; this plan does not fix it (Task 3 does not touch `flymanager/app/routes/settings.py`).

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/routes/stock.py tests/test_stock_sources.py
git commit -m "perf: version- and pipeline-signature-stamp ProviderMatchCache entries"
```

---

## Task 3: Bulk provider-match refresh via the shared backfill engine

**Files:**
- Modify: `flymanager/app/routes/stock.py` (add wrapper functions after Task 2's helpers)
- Modify: `flymanager/app/jobs/tasks.py:200-233`
- Test: `tests/test_provider_match_backfill.py`

**Interfaces:**
- Consumes: `backfill_materialized_cache` (Task 1), `_is_provider_match_cache_entry_valid`/`_build_provider_match_cache_envelope` (Task 2), `tests/mongo_fakes.py` `FakeDatabase`.
- Produces: `backfill_stock_provider_match_cache(collection, *, users=None, dry_run=False, force=False) -> dict` in `flymanager/app/routes/stock.py`, mirroring `backfill_stock_phenotype_cache`'s shape (`flymanager/utils/phenotypes/backfill.py:16-25`). `task_refresh_provider_caches(key, username)` in `jobs/tasks.py` keeps its existing signature; its body now calls this wrapper with `force=True` (matching its historical always-recompute behavior) instead of the old hand-rolled loop.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_provider_match_backfill.py
from unittest.mock import patch

import flymanager.app  # noqa: F401  (see tests/test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.app.routes.stock import (PROVIDER_MATCH_CACHE_FIELD,
                                         PROVIDER_MATCH_CACHE_VERSION,
                                         backfill_stock_provider_match_cache)

SOURCE_CONTEXT = {
    "sourceType": "BDSC", "sourceCollection": "Bloomington",
    "flyBaseStockID": "FBst0000017", "providerURL": "",
}


def _stock(uid, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "AssignedTo": "",
        "StockSource": "BDSC", "SourceID": "17", "SourceCollection": "Bloomington",
        "FlyBaseStockID": "FBst0000017", "Genotype": "w[*]",
        "ExternalRawGenotype": "", "AltReference": "", "Provenance": "Bloomington",
    }
    stock.update(overrides)
    return stock


def test_backfill_stock_provider_match_cache_builds_missing_entries(monkeypatch):
    db = FakeDatabase({"stocks": [_stock("s1")]})
    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig",
    )

    with patch(
        "flymanager.app.routes.stock.enrich_stock_source_context",
        return_value=SOURCE_CONTEXT,
    ), patch(
        "flymanager.app.routes.stock.find_external_stock_matches",
        return_value=[{"stockSource": "VIENNA", "sourceID": "4321"}],
    ):
        summary = backfill_stock_provider_match_cache(db["stocks"])

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
    stored = db["stocks"].find_one({"UniqueID": "s1"})[PROVIDER_MATCH_CACHE_FIELD]
    assert stored["version"] == PROVIDER_MATCH_CACHE_VERSION
    assert stored["pipelineSignature"] == "pipeline-sig"
    assert stored["candidates"] == [{"stockSource": "VIENNA", "sourceID": "4321"}]


def test_backfill_stock_provider_match_cache_skips_already_valid_entry(monkeypatch):
    from flymanager.app.routes.stock import _build_provider_match_cache_signature

    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig",
    )
    stock = _stock("s1")
    stock[PROVIDER_MATCH_CACHE_FIELD] = {
        "version": PROVIDER_MATCH_CACHE_VERSION,
        "pipelineSignature": "pipeline-sig",
        "signature": _build_provider_match_cache_signature(stock, SOURCE_CONTEXT),
        "candidates": [],
        "count": 0,
        "cachedAt": "2026-04-17 10:00",
    }
    db = FakeDatabase({"stocks": [stock]})

    with patch(
        "flymanager.app.routes.stock.enrich_stock_source_context",
        return_value=SOURCE_CONTEXT,
    ), patch(
        "flymanager.app.routes.stock.find_external_stock_matches",
        side_effect=AssertionError("must not recompute an already-valid cache"),
    ):
        summary = backfill_stock_provider_match_cache(db["stocks"])

    assert summary == {"scanned": 1, "updated": 0, "skipped_valid": 1, "errors": 0}


def test_backfill_stock_provider_match_cache_counts_errors_without_aborting(monkeypatch):
    db = FakeDatabase({"stocks": [_stock("bad"), _stock("good")]})
    monkeypatch.setattr(
        "flymanager.app.routes.stock.compute_flybase_pipeline_signature",
        lambda: "pipeline-sig",
    )

    def fake_find_matches(record):
        if record["UniqueID"] == "bad":
            raise RuntimeError("reference data unavailable")
        return []

    with patch(
        "flymanager.app.routes.stock.enrich_stock_source_context",
        return_value=SOURCE_CONTEXT,
    ), patch(
        "flymanager.app.routes.stock.find_external_stock_matches",
        side_effect=fake_find_matches,
    ):
        summary = backfill_stock_provider_match_cache(db["stocks"])

    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 0, "errors": 1}
    assert db["stocks"].find_one({"UniqueID": "bad"}).get(PROVIDER_MATCH_CACHE_FIELD) is None
    assert db["stocks"].find_one({"UniqueID": "good"}).get(PROVIDER_MATCH_CACHE_FIELD) is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_provider_match_backfill.py -v`
Expected: FAIL — `ImportError: cannot import name 'backfill_stock_provider_match_cache'`

- [ ] **Step 3: Implement the wrapper**

```python
# flymanager/app/routes/stock.py — add after _build_provider_match_cache_envelope (Task 2)
def _provider_match_cache_getter(record):
    source_context = enrich_stock_source_context(record)
    cache_payload = record.get(PROVIDER_MATCH_CACHE_FIELD)
    if _is_provider_match_cache_entry_valid(cache_payload, record, source_context):
        return cache_payload
    return None


def _provider_match_cache_builder(record):
    source_context = enrich_stock_source_context(record)
    candidates = find_external_stock_matches(record)
    return _build_provider_match_cache_envelope(record, source_context, candidates)


def backfill_stock_provider_match_cache(collection, *, users=None, dry_run=False, force=False):
    from flymanager.utils.materialized_cache import backfill_materialized_cache

    return backfill_materialized_cache(
        collection,
        cache_field=PROVIDER_MATCH_CACHE_FIELD,
        cache_getter=_provider_match_cache_getter,
        cache_builder=_provider_match_cache_builder,
        projection={
            "_id": 1, "UniqueID": 1, "User": 1, "AssignedTo": 1,
            "SourceID": 1, "StockSource": 1, "SourceCollection": 1,
            "FlyBaseStockID": 1, "Genotype": 1, "ExternalRawGenotype": 1,
            "AltReference": 1, "Provenance": 1, PROVIDER_MATCH_CACHE_FIELD: 1,
        },
        users=users,
        dry_run=dry_run,
        force=force,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_provider_match_backfill.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Rewrite `task_refresh_provider_caches` to use the wrapper**

```python
# flymanager/app/jobs/tasks.py — replace task_refresh_provider_caches (lines 200-233)
def task_refresh_provider_caches(key, username):
    def work(app, db):
        from flymanager.app.routes.stock import (PROVIDER_MATCH_CACHE_FIELD,
                                                  backfill_stock_provider_match_cache)

        summary = backfill_stock_provider_match_cache(db["stocks"], force=True)

        candidate_matches = sum(
            (doc.get(PROVIDER_MATCH_CACHE_FIELD) or {}).get("count", 0) or 0
            for doc in db["stocks"].find({}, {PROVIDER_MATCH_CACHE_FIELD: 1})
        )

        message = (
            "Provider cache refresh complete for all stocks: "
            f"{summary['updated']} refreshed, {candidate_matches} candidate matches cached, "
            f"{summary['errors']} errors, {summary['scanned']} scanned total."
        )
        write_activity(username, "Refreshed provider match cache for all stocks", db)
        return {"message": message, "summary": {**summary, "candidate_matches": candidate_matches}}

    _run(key, work)
```

`force=True` reproduces the old loop's behavior of unconditionally recomputing every stock's matches on every bulk run (it always called `_get_provider_match_payload(stock, refresh=True)`), rather than the new default of skipping already-valid entries.

- [ ] **Step 6: Run the background-jobs test suite to confirm no regressions**

Run: `python -m pytest tests/test_background_jobs.py tests/test_jobs_queue.py -v`
Expected: PASS — neither file references `task_refresh_provider_caches` directly today (confirmed via `grep -rln "task_refresh_provider_caches" tests/`), so this is a smoke check that the module still imports and other job tests are unaffected.

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/routes/stock.py flymanager/app/jobs/tasks.py tests/test_provider_match_backfill.py
git commit -m "perf: run the provider-match bulk refresh through backfill_materialized_cache"
```

---

## Task 4: Generic candidate-vs-record diff helper

**Files:**
- Modify: `flymanager/utils/mongo_records.py` (add after `build_owned_document_update_fields`, line 329)
- Test: `tests/test_mongo_records.py`

**Interfaces:**
- Produces: `diff_candidate_against_record(current_document, field_mapping) -> dict`. `field_mapping` is `{record_field: candidate_value}`. Returns `{field: {"current": str, "candidate": str, "conflict": bool}}` for every field whose (trimmed, `None`-as-empty) value would actually change; `conflict` is `True` when the current value is non-empty and differs from the candidate value (a genuine overwrite), `False` when the current value is empty (a fill-in-the-blank). Fields whose current and candidate values already match (after trimming) are omitted entirely. This is a pure function — no I/O — so any future "promote a cache candidate into a record" feature can reuse it without new plumbing (per the design spec's YAGNI note: only `apply_provider_match`, Task 5, uses it in this pass).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mongo_records.py — append
from flymanager.utils.mongo_records import diff_candidate_against_record


def test_diff_candidate_against_record_omits_fields_that_already_match():
    current = {"SourceID": "17", "FlyBaseStockID": "FBst0000017"}
    field_mapping = {"SourceID": "17", "FlyBaseStockID": "FBst0000017"}
    assert diff_candidate_against_record(current, field_mapping) == {}


def test_diff_candidate_against_record_flags_fill_in_the_blank_as_no_conflict():
    current = {"FlyBaseStockID": ""}
    field_mapping = {"FlyBaseStockID": "FBst0000017"}
    diff = diff_candidate_against_record(current, field_mapping)
    assert diff == {
        "FlyBaseStockID": {"current": "", "candidate": "FBst0000017", "conflict": False}
    }


def test_diff_candidate_against_record_flags_differing_existing_value_as_conflict():
    current = {"ExternalSupportStatus": "manual"}
    field_mapping = {"ExternalSupportStatus": "supported"}
    diff = diff_candidate_against_record(current, field_mapping)
    assert diff == {
        "ExternalSupportStatus": {"current": "manual", "candidate": "supported", "conflict": True}
    }


def test_diff_candidate_against_record_treats_none_as_empty():
    current = {"SourceCollection": None}
    field_mapping = {"SourceCollection": "Bloomington"}
    diff = diff_candidate_against_record(current, field_mapping)
    assert diff["SourceCollection"]["conflict"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_mongo_records.py -k diff_candidate_against_record -v`
Expected: FAIL — `ImportError: cannot import name 'diff_candidate_against_record'`

- [ ] **Step 3: Implement**

```python
# flymanager/utils/mongo_records.py — add after build_owned_document_update_fields (line 329)
def diff_candidate_against_record(current_document, field_mapping):
    """Compare a candidate's field values against a document's current values.

    Returns only fields that would actually change (after trimming, treating
    ``None``/``""`` as equivalent-empty). Each entry flags whether applying it
    would overwrite a genuinely different existing value (``conflict``) or
    only fill an empty field.
    """
    diff = {}
    for field, candidate_value in field_mapping.items():
        current_value = current_document.get(field)
        normalized_current = "" if current_value is None else str(current_value).strip()
        normalized_candidate = "" if candidate_value is None else str(candidate_value).strip()
        if normalized_current == normalized_candidate:
            continue
        diff[field] = {
            "current": normalized_current,
            "candidate": normalized_candidate,
            "conflict": bool(normalized_current),
        }
    return diff
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_mongo_records.py -k diff_candidate_against_record -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full `mongo_records` test suite to confirm no regressions**

Run: `python -m pytest tests/test_mongo_records.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/mongo_records.py tests/test_mongo_records.py
git commit -m "feat: add diff_candidate_against_record for cache-candidate apply flows"
```

---

## Task 5: `apply_provider_match` route

**Files:**
- Modify: `flymanager/app/routes/stock.py` (imports; new route after `reverse_search_stock`, line 1415)
- Test: `tests/test_stock_sources.py` (new tests, appended)

**Interfaces:**
- Consumes: `diff_candidate_against_record` (Task 4), `_is_provider_match_cache_entry_valid` (Task 2), `edit_stock` (already imported, `flymanager/utils/mongo/stocks.py:179`), `get_json_payload`/`parse_int_value` (already imported from `flymanager.app.security`).
- Produces: `POST /stock/apply_provider_match/<unique_id>`. JSON request body: `{"candidateIndex": int, "confirm": bool (optional, default false)}`. JSON responses:
  - `404` `{"error": "..."}` — stock not found for this user.
  - `403` `{"error": "..."}` — viewer cannot edit this stock.
  - `409` `{"error": "..."}` — cache is stale/missing, or `candidateIndex` is out of range for the current cached candidate list.
  - `200` `{"applied": false, "message": "..."}` — candidate's fields already match the record; nothing to do.
  - `200` `{"applied": false, "requiresConfirmation": true, "diff": {...}}` — a conflicting field would be overwritten; caller must re-POST with `"confirm": true`.
  - `200` `{"applied": true, "diff": {...}, "fields": {...}}` — write succeeded; `fields` is the flat `{record_field: new_value}` map the frontend uses to update the page in place.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stock_sources.py — append
def test_apply_provider_match_fills_blank_fields_without_confirmation(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1", "User": "admin", "ViewerCanEdit": True,
        "StockSource": "", "SourceCollection": "", "SourceID": "",
        "FlyBaseStockID": "", "ExternalSupportStatus": "",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
        "ProviderMatchCache": {
            "candidates": [
                {
                    "stockSource": "VIENNA", "sourceCollection": "Vienna",
                    "sourceID": "4321", "flyBaseStockID": "FBst0004321",
                    "supportStatus": "supported",
                }
            ],
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock", return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._is_provider_match_cache_entry_valid",
            return_value=True,
        ), patch(
            "flymanager.app.routes.stock.edit_stock", return_value=True,
        ) as edit_stock_mock, patch(
            "flymanager.app.routes.stock.write_activity",
        ):
            response = client.post(
                "/stock/apply_provider_match/UID1",
                json={"candidateIndex": 0},
            )

    assert response.status_code == 200
    body = response.get_json()
    assert body["applied"] is True
    assert body["fields"]["SourceID"] == "4321"
    assert body["fields"]["FlyBaseStockID"] == "FBst0004321"
    edit_stock_mock.assert_called_once()
    assert edit_stock_mock.call_args.args[0] == "admin"
    assert edit_stock_mock.call_args.args[1] == "UID1"


def test_apply_provider_match_requires_confirmation_for_conflicting_field(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1", "User": "admin", "ViewerCanEdit": True,
        "StockSource": "BDSC", "SourceCollection": "Bloomington", "SourceID": "17",
        "FlyBaseStockID": "FBst0000017", "ExternalSupportStatus": "manual",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
        "ProviderMatchCache": {
            "candidates": [
                {
                    "stockSource": "BDSC", "sourceCollection": "Bloomington",
                    "sourceID": "17", "flyBaseStockID": "FBst0000017",
                    "supportStatus": "supported",
                }
            ],
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock", return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._is_provider_match_cache_entry_valid",
            return_value=True,
        ), patch(
            "flymanager.app.routes.stock.edit_stock",
        ) as edit_stock_mock:
            response = client.post(
                "/stock/apply_provider_match/UID1",
                json={"candidateIndex": 0},
            )

    assert response.status_code == 200
    body = response.get_json()
    assert body["applied"] is False
    assert body["requiresConfirmation"] is True
    assert body["diff"]["ExternalSupportStatus"]["conflict"] is True
    edit_stock_mock.assert_not_called()


def test_apply_provider_match_writes_after_confirm(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1", "User": "admin", "ViewerCanEdit": True,
        "StockSource": "BDSC", "SourceCollection": "Bloomington", "SourceID": "17",
        "FlyBaseStockID": "FBst0000017", "ExternalSupportStatus": "manual",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
        "ProviderMatchCache": {
            "candidates": [
                {
                    "stockSource": "BDSC", "sourceCollection": "Bloomington",
                    "sourceID": "17", "flyBaseStockID": "FBst0000017",
                    "supportStatus": "supported",
                }
            ],
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock", return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._is_provider_match_cache_entry_valid",
            return_value=True,
        ), patch(
            "flymanager.app.routes.stock.edit_stock", return_value=True,
        ) as edit_stock_mock, patch(
            "flymanager.app.routes.stock.write_activity",
        ):
            response = client.post(
                "/stock/apply_provider_match/UID1",
                json={"candidateIndex": 0, "confirm": True},
            )

    assert response.status_code == 200
    body = response.get_json()
    assert body["applied"] is True
    assert body["fields"]["ExternalSupportStatus"] == "supported"
    edit_stock_mock.assert_called_once()


def test_apply_provider_match_rejects_out_of_range_candidate_index(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1", "User": "admin", "ViewerCanEdit": True,
        "ProviderMatchCache": {"candidates": []},
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock", return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._is_provider_match_cache_entry_valid",
            return_value=True,
        ):
            response = client.post(
                "/stock/apply_provider_match/UID1",
                json={"candidateIndex": 0},
            )

    assert response.status_code == 409


def test_apply_provider_match_rejects_when_viewer_cannot_edit(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {"UniqueID": "UID1", "User": "admin", "ViewerCanEdit": False}

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "somebody_else"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock", return_value=stock,
        ):
            response = client.post(
                "/stock/apply_provider_match/UID1",
                json={"candidateIndex": 0},
            )

    assert response.status_code == 403
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_stock_sources.py -k apply_provider_match -v`
Expected: FAIL — `404 NOT FOUND` (route doesn't exist yet).

- [ ] **Step 3: Add the import and the route**

```python
# flymanager/app/routes/stock.py — add to the existing import from flymanager.utils.mongo_records
from flymanager.utils.mongo_records import current_timestamp, diff_candidate_against_record
```

```python
# flymanager/app/routes/stock.py — add after reverse_search_stock (was line 1415)
@bp.route("/apply_provider_match/<unique_id>", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def apply_provider_match(unique_id):
    username = session.get("username")

    try:
        payload = get_json_payload()
        candidate_index = parse_int_value(
            payload.get("candidateIndex"), field_name="candidateIndex", minimum=0,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    confirmed = bool(payload.get("confirm"))

    stock = get_accessible_stock(username, unique_id, db, annotate=True)
    if not stock:
        return jsonify({"error": "Stock not found."}), 404
    if not stock.get("ViewerCanEdit"):
        return jsonify({"error": "Only the owner can apply a provider match."}), 403

    source_context = enrich_stock_source_context(stock)
    cache_payload = stock.get(PROVIDER_MATCH_CACHE_FIELD)
    if not _is_provider_match_cache_entry_valid(cache_payload, stock, source_context):
        return jsonify({
            "error": "Provider matches are out of date. Refresh matches and try again.",
        }), 409

    candidates = cache_payload.get("candidates") or []
    if candidate_index >= len(candidates):
        return jsonify({
            "error": "That match is no longer available. Refresh matches and try again.",
        }), 409

    candidate = candidates[candidate_index]
    field_mapping = {
        "StockSource": candidate.get("stockSource", ""),
        "SourceCollection": candidate.get("sourceCollection", ""),
        "SourceID": candidate.get("sourceID", ""),
        "FlyBaseStockID": candidate.get("flyBaseStockID", ""),
        "ExternalSupportStatus": candidate.get("supportStatus", ""),
    }

    diff = diff_candidate_against_record(stock, field_mapping)
    if not diff:
        return jsonify({
            "applied": False,
            "message": "This stock already matches the selected candidate.",
            "diff": {},
        })

    has_conflict = any(entry["conflict"] for entry in diff.values())
    if has_conflict and not confirmed:
        return jsonify({"applied": False, "requiresConfirmation": True, "diff": diff})

    updates = {field: entry["candidate"] for field, entry in diff.items()}
    success = edit_stock(stock["User"], unique_id, db, updates, refresh_vials=False)
    if not success:
        return jsonify({"error": "Unable to update stock record."}), 500

    match_label = candidate.get("sourceCollection") or candidate.get("stockSource") or "provider"
    write_activity(
        username,
        f"Applied {match_label} {candidate.get('sourceID', '')} provider match to stock {unique_id}",
        db,
    )

    return jsonify({"applied": True, "diff": diff, "fields": updates})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_stock_sources.py -k apply_provider_match -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full stock-sources test suite to confirm no regressions**

Run: `python -m pytest tests/test_stock_sources.py -v`
Expected: PASS (aside from the pre-existing unrelated failure noted in Task 2, Step 6)

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/routes/stock.py tests/test_stock_sources.py
git commit -m "feat: add apply_provider_match route to promote a cached match into the stock record"
```

---

## Task 6: "Apply" button and confirm flow in the stock view

**Files:**
- Modify: `flymanager/app/templates/stock/view_stock.html` (metadata cells around lines 518-534; `renderProviderMatches`, lines 1018-1065)

**Interfaces:**
- Consumes: `POST /stock/apply_provider_match/<unique_id>` (Task 5).
- Produces: an "Apply" button per rendered candidate, and four now-addressable metadata `<p>` elements (`sourceDisplay`, `sourceIdDisplay`, `flyBaseStockIdDisplay`, `supportStatusDisplay`) that get updated in place on a successful apply, so the page doesn't need a full reload.

This task is template/JS only — there's no Python test to write. Verify manually per Step 3.

- [ ] **Step 1: Give the four "Ordering Metadata" display cells stable ids**

```html
<!-- flymanager/app/templates/stock/view_stock.html — replace lines 518-534 -->
        <div class="stock-source-grid">
            <div class="stock-source-cell">
                <p class="stock-summary-label">Source</p>
                <p id="sourceDisplay">{{ stock_data.sourceType or 'OTHER' }}{% if stock_data.sourceCollection %} / {{ stock_data.sourceCollection }}{% endif %}</p>
            </div>
            <div class="stock-source-cell">
                <p class="stock-summary-label">Source ID</p>
                <p id="sourceIdDisplay">{{ stock_data.sourceID or 'Unavailable' }}</p>
            </div>
            <div class="stock-source-cell">
                <p class="stock-summary-label">FlyBase Stock ID</p>
                <p id="flyBaseStockIdDisplay">{{ stock_data.flyBaseStockID or 'Unavailable' }}</p>
            </div>
            <div class="stock-source-cell">
                <p class="stock-summary-label">Support Status</p>
                <p id="supportStatusDisplay">{{ stock_data.externalSupportStatus or 'manual' }}</p>
            </div>
        </div>
```

- [ ] **Step 2: Add the route URL constant, following the existing `reverseSearchUrl` convention**

```javascript
// flymanager/app/templates/stock/view_stock.html — add next to reverseSearchUrl (line 845)
    const applyProviderMatchUrl = "{{ url_for('stock.apply_provider_match', unique_id=stock_data.uniqueID) }}";
```

- [ ] **Step 3: Add the "Apply" button, confirmation flow, and in-place update to `renderProviderMatches`**

```javascript
// flymanager/app/templates/stock/view_stock.html — replace renderProviderMatches (lines 1018-1065)
    function applyProviderMatch(candidateIndex, candidate, statusNote, confirmed) {
        return fetch(applyProviderMatchUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ candidateIndex: candidateIndex, confirm: !!confirmed }),
        })
            .then(response => response.json().then(body => ({ status: response.status, body })))
            .then(({ status, body }) => {
                if (status !== 200) {
                    statusNote.textContent = body.error || 'Unable to apply this match.';
                    return;
                }
                if (body.requiresConfirmation) {
                    const summary = Object.entries(body.diff)
                        .map(([field, change]) => `${field}: "${change.current}" -> "${change.candidate}"`)
                        .join('; ');
                    statusNote.textContent = `This will change ${summary}. Click Apply again to confirm.`;
                    statusNote.dataset.pendingCandidateIndex = String(candidateIndex);
                    return;
                }
                if (!body.applied) {
                    statusNote.textContent = body.message || 'Nothing to apply.';
                    return;
                }
                if ('StockSource' in body.fields || 'SourceCollection' in body.fields) {
                    const sourceType = body.fields.StockSource || document.getElementById('sourceDisplay').dataset.rawSourceType || 'OTHER';
                    const sourceCollection = body.fields.SourceCollection || '';
                    document.getElementById('sourceDisplay').textContent = sourceCollection
                        ? `${sourceType} / ${sourceCollection}`
                        : sourceType;
                }
                if ('SourceID' in body.fields) {
                    document.getElementById('sourceIdDisplay').textContent = body.fields.SourceID || 'Unavailable';
                }
                if ('FlyBaseStockID' in body.fields) {
                    document.getElementById('flyBaseStockIdDisplay').textContent = body.fields.FlyBaseStockID || 'Unavailable';
                }
                if ('ExternalSupportStatus' in body.fields) {
                    document.getElementById('supportStatusDisplay').textContent = body.fields.ExternalSupportStatus || 'manual';
                }
                statusNote.textContent = 'Match applied.';
            })
            .catch(() => {
                statusNote.textContent = 'An error occurred while applying this match.';
            });
    }

    function renderProviderMatches(candidates, options) {
        const config = options || {};
        providerMatchesPanel.innerHTML = '';
        providerMatchesPanel.classList.remove('is-hidden');

        if (!candidates.length) {
            const emptyState = document.createElement('p');
            emptyState.className = 'stock-match-copy';
            emptyState.textContent = 'No provider-backed candidates were found for this stock.';
            providerMatchesPanel.appendChild(emptyState);
            return;
        }

        candidates.forEach((candidate, candidateIndex) => {
            const item = document.createElement('div');
            item.className = 'stock-match-item';

            const meta = document.createElement('div');

            const title = document.createElement('strong');
            title.textContent = `${candidate.sourceCollection || candidate.stockSource} ${candidate.sourceID}`;

            const subtitle = document.createElement('p');
            subtitle.className = 'stock-match-copy';
            subtitle.textContent = `${candidate.name || 'Unnamed stock'} • Score ${candidate.matchScore}`;

            const reasons = document.createElement('p');
            reasons.className = 'stock-match-copy';
            reasons.textContent = (candidate.matchReasons || []).join(' • ');

            const statusNote = document.createElement('p');
            statusNote.className = 'stock-match-copy';

            meta.appendChild(title);
            meta.appendChild(subtitle);
            meta.appendChild(reasons);
            meta.appendChild(statusNote);
            item.appendChild(meta);

            if (canEditRecord) {
                const applyBtn = document.createElement('button');
                applyBtn.type = 'button';
                applyBtn.className = 'btn btn-outline-primary btn-sm';
                applyBtn.textContent = 'Apply';
                applyBtn.addEventListener('click', () => {
                    const pendingIndex = statusNote.dataset.pendingCandidateIndex;
                    const isConfirming = pendingIndex === String(candidateIndex);
                    applyProviderMatch(candidateIndex, candidate, statusNote, isConfirming);
                });
                item.appendChild(applyBtn);
            }

            if (candidate.providerURL) {
                const link = document.createElement('a');
                link.href = candidate.providerURL;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                link.className = 'btn btn-outline-info btn-sm';
                link.textContent = 'Open';
                item.appendChild(link);
            }

            providerMatchesPanel.appendChild(item);
        });
    }
```

`fetch` here doesn't set an explicit `X-CSRFToken` header — `base.html`'s global fetch patch (`flymanager/app/templates/base.html:1583-1584`) already injects it into same-origin requests automatically, matching how `mark_ordered_stocks` and other JSON POST routes in this app already work without route-level `@csrf.exempt`.

- [ ] **Step 4: Manually verify in the running app**

Use the `run-flymanager` skill to start the app, then:
1. Open a stock detail page for a stock with `ViewerCanEdit` true and a blank `Source ID`/`FlyBase Stock ID`.
2. Click "Find Provider Matches", then click "Apply" on a candidate with only blank fields to fill — confirm it applies immediately (`applied: true`) and the four metadata cells update without a page reload.
3. Repeat on a stock that already has a different `Support Status` set — confirm the first click shows the confirmation message instead of applying, and a second click on the same candidate applies it.
4. Reload the page and confirm the applied values persisted (i.e. they came from the saved stock document, not just the DOM patch).

- [ ] **Step 5: Commit**

```bash
git add flymanager/app/templates/stock/view_stock.html
git commit -m "feat: add Apply action to provider match candidates in the stock view"
```
