# Cache Invalidation: Partial Recalculation & Guarded Full Recompute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Run this plan after:** `docs/superpowers/plans/2026-08-14-provider-match-cache-unification.md`. That plan puts `ProviderMatchCache` onto the shared `materialized_cache.py` backfill engine and gives it a `version`/`pipelineSignature` envelope, and adds `backfill_stock_provider_match_cache(collection, *, users=None, dry_run=False, force=False)` in `flymanager/app/routes/stock.py`. This plan's Task 4 and Task 6 call that function directly — do not start until it exists on the branch. Line numbers cited below are current as of this writing; if the unification plan shifted them, use the named function/route to locate the insertion point instead of the literal line number.

**Goal:** Close three cache-invalidation gaps left after the unification plan: (1) editing a stock's genotype doesn't refresh the caches of crosses that reference it as a parent, (2) FlyBase reference-data refreshes don't trigger any rebuild of dependent caches, and (3) the only existing "recompute everything" action is unconditional and unguarded, with no equivalent for two of the three caches. This plan adds eager cross-record propagation on stock edit, an auto-triggered targeted rebuild after reference-data refresh, and a single guarded (typed-phrase + 24h cooldown + audit-logged) force-full-recompute capability shared by all three caches.

**Architecture:** No new services or collections. A new helper in `flymanager/utils/mongo/crosses.py` propagates a stock's genotype change into dependent crosses' denormalized fields and caches, called from `edit_stock`. `task_refresh_flybase_reference_data`'s existing background-job closure gains a call to a new pure helper that runs all five cache backfills at `force=False`. A new `flymanager/utils/mongo/cache_force_refresh.py` module tracks per-cache cooldown state in the existing singleton `settings` collection; a new admin route and background task use it to guard `force=True` recomputes, replacing the existing unconditional provider-match "refresh all" path.

**Tech Stack:** Flask, PyMongo, pytest, `tests/mongo_fakes.py` (`FakeDatabase`/`FakeCollection`), the existing `_make_app(monkeypatch)` Flask-test-client fixture pattern used across `tests/test_stock_sources.py` / `tests/test_flybase_admin.py`.

**Spec:** `docs/superpowers/specs/2026-08-14-cache-invalidation-partial-recalculation-design.md`

## Global Constraints

- No new dependencies, no new collections. Cooldown state lives in the existing singleton `settings` collection (`flymanager/utils/mongo/settings.py`); audit entries reuse the existing `write_activity` log with a `"[FORCE-RECOMPUTE]"` text prefix — no schema change to either.
- Every automated cascade write (propagation, backfills) goes through the existing `apply_updates_to_owned_document` / `backfill_materialized_cache` machinery — no new write path is invented.
- `FakeDatabase` (`tests/mongo_fakes.py`) does not support attribute-style collection access (`db.activity`), only `db["activity"]`. `write_activity` uses `db.activity.insert_one(...)`, so any test exercising a code path that calls `write_activity` against a `FakeDatabase` must patch `write_activity` at the call site's module, not let it execute for real.
- `FakeCollection.find`/`find_one` match top-level `"$or"` by ignoring sibling keys (a bug in the test double, not real Mongo) — any query combining `"$or"` with another filter (e.g. `"User"`) must be wrapped in an explicit `"$and"` so both the fake and real Mongo evaluate it correctly.
- Cooldown/timestamp comparisons take an optional `now` keyword (defaulting to `datetime.now()` at call time) so tests can pass a fixed value instead of monkeypatching the `datetime` module.
- The cooldown window is 24 hours per cache, tracked independently per cache key (`phenotype`, `standardization`, `provider_match`).

---

## Task 1: Cross parent-lookup indexes

**Files:**
- Modify: `flymanager/utils/mongo/db.py:51-73` (`ensure_mongo_indexes`)
- Test: `tests/test_mongo_indexes.py`

**Interfaces:**
- Produces: two new indexes on the `crosses` collection — `{User: 1, MaleUniqueID: 1}` (name `crosses_user_male_uid`) and `{User: 1, FemaleUniqueID: 1}` (name `crosses_user_female_uid`). Task 2's propagation lookup query relies on these existing so its `find({"$and": [{"User": ...}, {"$or": [{"MaleUniqueID": ...}, {"FemaleUniqueID": ...}]}]})` stays cheap.

No index like this exists today — `ensure_mongo_indexes` only has `(User, UniqueID)` / `(AssignedTo, UniqueID)` / status+tray compounds for `crosses`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mongo_indexes.py
from unittest.mock import patch

from tests.mongo_fakes import FakeCollection, FakeDatabase
from flymanager.utils.mongo.db import ensure_mongo_indexes


def test_ensure_mongo_indexes_creates_cross_parent_lookup_indexes():
    db = FakeDatabase()
    calls = []
    original_create_index = FakeCollection.create_index

    def recording_create_index(self, *args, **kwargs):
        calls.append((self.name, args, kwargs))
        return original_create_index(self, *args, **kwargs)

    with patch.object(FakeCollection, "create_index", recording_create_index):
        ensure_mongo_indexes(db)

    assert (
        "crosses",
        ([("User", 1), ("MaleUniqueID", 1)],),
        {"name": "crosses_user_male_uid"},
    ) in calls
    assert (
        "crosses",
        ([("User", 1), ("FemaleUniqueID", 1)],),
        {"name": "crosses_user_female_uid"},
    ) in calls
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_mongo_indexes.py -v`
Expected: FAIL — the two new index-creation calls never happen, so the `in calls` assertions fail.

- [ ] **Step 3: Add the two indexes**

```python
# flymanager/utils/mongo/db.py — inside ensure_mongo_indexes, immediately after the
# existing crosses_assigned_status_tray index line
    db["crosses"].create_index([("User", 1), ("MaleUniqueID", 1)], name="crosses_user_male_uid")
    db["crosses"].create_index([("User", 1), ("FemaleUniqueID", 1)], name="crosses_user_female_uid")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_mongo_indexes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/mongo/db.py tests/test_mongo_indexes.py
git commit -m "perf: index crosses on (User, MaleUniqueID) and (User, FemaleUniqueID)"
```

---

## Task 2: `propagate_stock_genotype_to_crosses` helper

**Files:**
- Modify: `flymanager/utils/mongo/crosses.py` (add after `edit_cross`, currently ending around line 240)
- Test: `tests/test_cross_propagation.py`

**Interfaces:**
- Consumes: `build_cross_phenotype_cache` (imported from `flymanager.utils.phenotypes.predictor`, already imported at the top of `crosses.py`), `build_cross_standardization_cache` (local wrapper already defined at `crosses.py:22-27`), `apply_updates_to_owned_document` (already imported at the top of `crosses.py`).
- Produces: `propagate_stock_genotype_to_crosses(user, stock_unique_id, new_genotype, db) -> dict`. Returns `{"crosses_updated": int, "errors": int}`. For every cross owned by `user` where `stock_unique_id` is `MaleUniqueID` and/or `FemaleUniqueID`, updates the matching denormalized genotype field(s) to `new_genotype`, rebuilds `PhenotypeCache`/`StandardizationCache` from the resulting Male/Female genotype pair, and persists via `apply_updates_to_owned_document("crosses", user, cross_uid, db, updates, log_activity=True)` — not via `edit_cross`, since that also triggers a vial refresh that doesn't apply here. A per-cross exception is caught, counted in `errors`, and does not stop the remaining crosses from being processed (same containment pattern `materialized_cache.py`'s `backfill_materialized_cache` already uses).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cross_propagation.py
from unittest.mock import patch

import flymanager.app  # noqa: F401  (see tests/test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.crosses import propagate_stock_genotype_to_crosses


def _cross(uid, male_uid, female_uid, male_genotype="w[*]", female_genotype="w[*]", user="alice"):
    return {
        "UniqueID": uid, "User": user, "AssignedTo": "",
        "MaleUniqueID": male_uid, "FemaleUniqueID": female_uid,
        "MaleGenotype": male_genotype, "FemaleGenotype": female_genotype,
        "PhenotypeCache": {"old": True}, "StandardizationCache": {"old": True},
    }


def test_propagate_updates_cross_where_stock_is_male_parent():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID1", female_uid="SID2")],
    })

    with patch(
        "flymanager.utils.mongo.crosses.build_cross_phenotype_cache",
        return_value={"phenotype": "new"},
    ) as build_phenotype, patch(
        "flymanager.utils.mongo.crosses.build_cross_standardization_cache",
        return_value={"standardization": "new"},
    ) as build_standardization:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]; CyO/+", db)

    assert summary == {"crosses_updated": 1, "errors": 0}
    updated = db["crosses"].find_one({"UniqueID": "CID1"})
    assert updated["MaleGenotype"] == "w[*]; CyO/+"
    assert updated["FemaleGenotype"] == "w[*]"
    assert updated["PhenotypeCache"] == {"phenotype": "new"}
    assert updated["StandardizationCache"] == {"standardization": "new"}
    build_phenotype.assert_called_once_with("w[*]; CyO/+", "w[*]")
    build_standardization.assert_called_once_with("w[*]; CyO/+", "w[*]")


def test_propagate_updates_cross_where_stock_is_female_parent():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID2", female_uid="SID1")],
    })

    with patch(
        "flymanager.utils.mongo.crosses.build_cross_phenotype_cache",
        return_value={"phenotype": "new"},
    ), patch(
        "flymanager.utils.mongo.crosses.build_cross_standardization_cache",
        return_value={"standardization": "new"},
    ):
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]; CyO/+", db)

    assert summary == {"crosses_updated": 1, "errors": 0}
    updated = db["crosses"].find_one({"UniqueID": "CID1"})
    assert updated["FemaleGenotype"] == "w[*]; CyO/+"
    assert updated["MaleGenotype"] == "w[*]"


def test_propagate_handles_self_cross_updating_both_sides():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID1", female_uid="SID1")],
    })

    with patch(
        "flymanager.utils.mongo.crosses.build_cross_phenotype_cache",
        return_value={"phenotype": "new"},
    ), patch(
        "flymanager.utils.mongo.crosses.build_cross_standardization_cache",
        return_value={"standardization": "new"},
    ):
        propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]; CyO/+", db)

    updated = db["crosses"].find_one({"UniqueID": "CID1"})
    assert updated["MaleGenotype"] == "w[*]; CyO/+"
    assert updated["FemaleGenotype"] == "w[*]; CyO/+"


def test_propagate_is_noop_for_stock_with_no_dependent_crosses():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID9", female_uid="SID8")],
    })

    with patch("flymanager.utils.mongo.crosses.build_cross_phenotype_cache") as build_phenotype:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]", db)

    assert summary == {"crosses_updated": 0, "errors": 0}
    build_phenotype.assert_not_called()


def test_propagate_only_touches_crosses_owned_by_same_user():
    db = FakeDatabase({
        "crosses": [_cross("CID1", male_uid="SID1", female_uid="SID2", user="bob")],
    })

    with patch("flymanager.utils.mongo.crosses.build_cross_phenotype_cache") as build_phenotype:
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "w[*]", db)

    assert summary == {"crosses_updated": 0, "errors": 0}
    build_phenotype.assert_not_called()


def test_propagate_counts_errors_without_aborting_other_crosses():
    db = FakeDatabase({
        "crosses": [
            _cross("bad", male_uid="SID1", female_uid="SID2", female_genotype="raise-trigger"),
            _cross("good", male_uid="SID1", female_uid="SID3", female_genotype="w[*]"),
        ],
    })

    def fake_build_phenotype(male_genotype, female_genotype):
        if female_genotype == "raise-trigger":
            raise RuntimeError("bad genotype")
        return {"phenotype": "new"}

    with patch(
        "flymanager.utils.mongo.crosses.build_cross_phenotype_cache",
        side_effect=fake_build_phenotype,
    ), patch(
        "flymanager.utils.mongo.crosses.build_cross_standardization_cache",
        return_value={"standardization": "new"},
    ):
        summary = propagate_stock_genotype_to_crosses("alice", "SID1", "edited-genotype", db)

    assert summary == {"crosses_updated": 1, "errors": 1}
    assert db["crosses"].find_one({"UniqueID": "good"})["MaleGenotype"] == "edited-genotype"
    assert db["crosses"].find_one({"UniqueID": "bad"})["MaleGenotype"] != "edited-genotype"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cross_propagation.py -v`
Expected: FAIL — `ImportError: cannot import name 'propagate_stock_genotype_to_crosses'`

- [ ] **Step 3: Implement the helper**

```python
# flymanager/utils/mongo/crosses.py — add after edit_cross
def propagate_stock_genotype_to_crosses(user, stock_unique_id, new_genotype, db):
    """Refresh denormalized genotype + caches on crosses referencing this stock as a parent.

    Called after a stock's own Genotype is edited, so dependent crosses don't
    go stale until someone happens to edit the cross directly. Writes go
    through apply_updates_to_owned_document directly (not edit_cross) to skip
    edit_cross's vial-refresh side effect, which doesn't apply here.
    """
    query = {
        "$and": [
            {"User": user},
            {"$or": [
                {"MaleUniqueID": stock_unique_id},
                {"FemaleUniqueID": stock_unique_id},
            ]},
        ]
    }

    summary = {"crosses_updated": 0, "errors": 0}
    for cross in db["crosses"].find(query):
        try:
            is_male_parent = cross.get("MaleUniqueID") == stock_unique_id
            is_female_parent = cross.get("FemaleUniqueID") == stock_unique_id

            male_genotype = new_genotype if is_male_parent else cross.get("MaleGenotype", "")
            female_genotype = new_genotype if is_female_parent else cross.get("FemaleGenotype", "")

            updates = {}
            if is_male_parent:
                updates["MaleGenotype"] = male_genotype
            if is_female_parent:
                updates["FemaleGenotype"] = female_genotype

            updates["PhenotypeCache"] = build_cross_phenotype_cache(male_genotype, female_genotype)
            updates["StandardizationCache"] = build_cross_standardization_cache(male_genotype, female_genotype)

            success, _ = apply_updates_to_owned_document(
                "crosses", user, cross["UniqueID"], db, updates, log_activity=True,
            )
            if success:
                summary["crosses_updated"] += 1
        except Exception:
            summary["errors"] += 1

    return summary
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cross_propagation.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/mongo/crosses.py tests/test_cross_propagation.py
git commit -m "feat: add propagate_stock_genotype_to_crosses helper"
```

---

## Task 3: Wire propagation into `edit_stock`

**Files:**
- Modify: `flymanager/utils/mongo/stocks.py:179-223` (`edit_stock`)
- Test: `tests/test_edit_stock_propagation.py`

**Interfaces:**
- Consumes: `propagate_stock_genotype_to_crosses` (Task 2), imported lazily inside `edit_stock` (same lazy-import-to-avoid-circular-import convention `stocks.py` already uses for `build_stock_standardization_cache`).
- Produces: no signature change to `edit_stock` — it still returns `bool`. When a `Genotype` update succeeds, it now also calls `propagate_stock_genotype_to_crosses(user, uid, prepared_updates["Genotype"], db)` after the stock's own write succeeds. A failed stock write (`success is False`) does not trigger propagation.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_edit_stock_propagation.py
from unittest.mock import patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.stocks import edit_stock


def _stock(uid, user="alice", genotype="w[*]"):
    return {
        "UniqueID": uid, "User": user, "AssignedTo": "",
        "Genotype": genotype, "PhenotypeCache": {}, "StandardizationCache": {},
    }


def test_edit_stock_propagates_genotype_change_to_dependent_crosses():
    db = FakeDatabase({"stocks": [_stock("SID1")]})

    with patch(
        "flymanager.utils.mongo.stocks.build_stock_phenotype_cache",
        return_value={"phenotype": "new"},
    ), patch(
        "flymanager.utils.mongo.stocks.build_stock_standardization_cache",
        return_value={"standardization": "new"},
    ), patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
        return_value={"crosses_updated": 2, "errors": 0},
    ) as propagate:
        success = edit_stock("alice", "SID1", db, {"Genotype": "w[*]; CyO/+"}, refresh_vials=False)

    assert success is True
    propagate.assert_called_once_with("alice", "SID1", "w[*]; CyO/+", db)


def test_edit_stock_does_not_propagate_when_genotype_unchanged():
    db = FakeDatabase({"stocks": [_stock("SID1")]})

    with patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
    ) as propagate:
        success = edit_stock("alice", "SID1", db, {"AssignedTo": "bob"}, refresh_vials=False)

    assert success is True
    propagate.assert_not_called()


def test_edit_stock_does_not_propagate_when_write_fails():
    db = FakeDatabase({"stocks": [_stock("SID1")]})

    with patch(
        "flymanager.utils.mongo.stocks.build_stock_phenotype_cache",
        return_value={"phenotype": "new"},
    ), patch(
        "flymanager.utils.mongo.stocks.build_stock_standardization_cache",
        return_value={"standardization": "new"},
    ), patch(
        "flymanager.utils.mongo.crosses.propagate_stock_genotype_to_crosses",
    ) as propagate:
        success = edit_stock("alice", "SID_MISSING", db, {"Genotype": "w[*]; CyO/+"}, refresh_vials=False)

    assert success is False
    propagate.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_edit_stock_propagation.py -v`
Expected: FAIL — `propagate.assert_called_once_with(...)` fails because `edit_stock` never calls it yet.

- [ ] **Step 3: Wire the call into `edit_stock`**

```python
# flymanager/utils/mongo/stocks.py — replace edit_stock (lines 179-223)
def edit_stock(user, uid, db, updates, log_activity=True, refresh_vials=True):
    """
    Edit specific fields of a stock in the user's stock collection.

    Parameters:
    user: str
        The username of the user.
    uid: str
        The unique identifier of the stock.
    db: pymongo.database.Database
        The MongoDB database instance.
    updates: dict
        A dictionary of the fields to update and their new values.
    log_activity: bool
        Whether to log the activity of the stock update.
    refresh_vials: bool
        Whether to refresh the vials of the stock.

    Returns:
    bool
        True if the stock was updated, False if not found.
    """

    prepared_updates = dict(updates)
    if "Genotype" in prepared_updates:
        prepared_updates["PhenotypeCache"] = build_stock_phenotype_cache(
            prepared_updates["Genotype"]
        )
        prepared_updates["StandardizationCache"] = build_stock_standardization_cache(
            prepared_updates["Genotype"]
        )

    success, current_stock = apply_updates_to_owned_document(
        "stocks",
        user,
        uid,
        db,
        prepared_updates,
        log_activity=log_activity,
    )

    if success and "Genotype" in prepared_updates:
        from flymanager.utils.mongo.crosses import \
            propagate_stock_genotype_to_crosses
        propagate_stock_genotype_to_crosses(user, uid, prepared_updates["Genotype"], db)

    if success and refresh_vials and current_stock:
        update_stock_vials(current_stock, user, db)

    return success
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_edit_stock_propagation.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full stocks test suite to confirm no regressions**

Run: `python -m pytest tests/test_bulk_operations.py tests/test_tray_moves.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/mongo/stocks.py tests/test_edit_stock_propagation.py
git commit -m "feat: propagate stock genotype edits into dependent cross caches"
```

---

## Task 4: Auto-triggered targeted rebuild after FlyBase reference-data refresh

**Files:**
- Modify: `flymanager/app/jobs/tasks.py:266-283` (`task_refresh_flybase_reference_data`); add `_rebuild_caches_after_flybase_refresh` as a new module-level function in the same file.
- Test: `tests/test_flybase_cache_rebuild_chain.py`

**Interfaces:**
- Consumes: `backfill_stock_phenotype_cache`/`backfill_cross_phenotype_cache` (`flymanager.utils.phenotypes.backfill`), `backfill_stock_standardization_cache`/`backfill_cross_standardization_cache` (`flymanager.app.services.standardization_backfill`), `backfill_stock_provider_match_cache` (`flymanager.app.routes.stock`, from the unification plan) — all imported lazily inside the new function, matching the lazy-import convention every other task in this file already uses.
- Produces: `_rebuild_caches_after_flybase_refresh(db) -> dict` with keys `stock_phenotype`, `cross_phenotype`, `stock_standardization`, `cross_standardization`, `stock_provider_match`. Each value is that wrapper's own summary dict on success, or `{"error": True}` if that one wrapper raised — one wrapper's exception does not stop the others. `task_refresh_flybase_reference_data`'s `work` closure calls this after the existing reference-data download succeeds and folds the result into the job's returned message/result dict; the task's outer signature is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_flybase_cache_rebuild_chain.py
from unittest.mock import ANY, patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.app.jobs.tasks import _rebuild_caches_after_flybase_refresh


def test_rebuild_caches_after_flybase_refresh_calls_all_five_wrappers_with_force_false():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.utils.phenotypes.backfill.backfill_stock_phenotype_cache",
        return_value={"scanned": 1, "updated": 0, "skipped_valid": 1},
    ) as stock_phenotype, patch(
        "flymanager.utils.phenotypes.backfill.backfill_cross_phenotype_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_phenotype, patch(
        "flymanager.app.services.standardization_backfill.backfill_stock_standardization_cache",
        return_value={"scanned": 1, "updated": 0, "skipped_valid": 1},
    ) as stock_standardization, patch(
        "flymanager.app.services.standardization_backfill.backfill_cross_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_standardization, patch(
        "flymanager.app.routes.stock.backfill_stock_provider_match_cache",
        return_value={"scanned": 1, "updated": 0, "skipped_valid": 1, "errors": 0},
    ) as stock_provider_match:
        results = _rebuild_caches_after_flybase_refresh(db)

    stock_phenotype.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    cross_phenotype.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    stock_standardization.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    cross_standardization.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    stock_provider_match.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    assert results["stock_phenotype"]["skipped_valid"] == 1
    assert results["stock_provider_match"]["scanned"] == 1


def test_rebuild_caches_after_flybase_refresh_isolates_wrapper_failures():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.utils.phenotypes.backfill.backfill_stock_phenotype_cache",
        side_effect=RuntimeError("boom"),
    ), patch(
        "flymanager.utils.phenotypes.backfill.backfill_cross_phenotype_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_phenotype, patch(
        "flymanager.app.services.standardization_backfill.backfill_stock_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ), patch(
        "flymanager.app.services.standardization_backfill.backfill_cross_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ), patch(
        "flymanager.app.routes.stock.backfill_stock_provider_match_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0, "errors": 0},
    ):
        results = _rebuild_caches_after_flybase_refresh(db)

    assert results["stock_phenotype"] == {"error": True}
    cross_phenotype.assert_called_once()
    assert results["cross_phenotype"]["scanned"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_flybase_cache_rebuild_chain.py -v`
Expected: FAIL — `ImportError: cannot import name '_rebuild_caches_after_flybase_refresh'`

- [ ] **Step 3: Implement the helper and chain it into `task_refresh_flybase_reference_data`**

```python
# flymanager/app/jobs/tasks.py — add as a new module-level function
def _rebuild_caches_after_flybase_refresh(db):
    """Run all five materialized-cache backfills at force=False after a FlyBase
    reference-data refresh. Each wrapper is isolated in its own try/except so
    one failing doesn't block the others; force=False means the cost is
    proportional to how much reference data actually changed, since a valid
    cache entry (unchanged pipelineSignature) is skipped, not rebuilt."""
    from flymanager.app.routes.stock import backfill_stock_provider_match_cache
    from flymanager.app.services.standardization_backfill import (
        backfill_cross_standardization_cache, backfill_stock_standardization_cache)
    from flymanager.utils.phenotypes.backfill import (
        backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)

    wrappers = {
        "stock_phenotype": lambda: backfill_stock_phenotype_cache(
            db["stocks"], users=None, dry_run=False, force=False),
        "cross_phenotype": lambda: backfill_cross_phenotype_cache(
            db["crosses"], users=None, dry_run=False, force=False),
        "stock_standardization": lambda: backfill_stock_standardization_cache(
            db["stocks"], users=None, dry_run=False, force=False),
        "cross_standardization": lambda: backfill_cross_standardization_cache(
            db["crosses"], users=None, dry_run=False, force=False),
        "stock_provider_match": lambda: backfill_stock_provider_match_cache(
            db["stocks"], users=None, dry_run=False, force=False),
    }

    results = {}
    for label, run in wrappers.items():
        try:
            results[label] = run()
        except Exception:
            results[label] = {"error": True}
    return results
```

```python
# flymanager/app/jobs/tasks.py — replace task_refresh_flybase_reference_data (lines 266-283)
def task_refresh_flybase_reference_data(key, username):
    def work(app, db):
        from flymanager.app.services import flybase as flybase_service

        report = flybase_service.manual_refresh_flybase_reference_data(app)
        downloaded_count = sum(
            1 for item in report["download_report"]["results"] if item.get("status") == "downloaded"
        )
        skipped_count = sum(
            1 for item in report["download_report"]["results"] if item.get("status") == "skipped"
        )

        cache_rebuild = _rebuild_caches_after_flybase_refresh(db)
        rebuild_summary = ", ".join(
            f"{label} failed" if "error" in result else f"{label}: {result.get('updated', 0)} updated"
            for label, result in cache_rebuild.items()
        )

        message = (
            f"FlyBase release {report['release']} refresh completed: "
            f"{downloaded_count} files downloaded, {skipped_count} reused, gene metadata refreshed. "
            f"Cache rebuild — {rebuild_summary}."
        )
        return {"message": message, "release": report["release"], "cacheRebuild": cache_rebuild}

    _run(key, work)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_flybase_cache_rebuild_chain.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the FlyBase admin test suite to confirm no regressions**

Run: `python -m pytest tests/test_flybase_admin.py -v`
Expected: PASS (existing tests for this route mock `flybase_service.manual_refresh_flybase_reference_data` and don't assert on cache-rebuild calls, so they remain green; they will not fail from the added closure work).

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/jobs/tasks.py tests/test_flybase_cache_rebuild_chain.py
git commit -m "feat: auto-trigger targeted cache rebuild after FlyBase reference-data refresh"
```

---

## Task 5: `cache_force_refresh` helper module

**Files:**
- Create: `flymanager/utils/mongo/cache_force_refresh.py`
- Test: `tests/test_cache_force_refresh.py`

**Interfaces:**
- Consumes: `get_settings`/`update_settings` (`flymanager.utils.mongo.settings`).
- Produces:
  - `CACHE_FORCE_REFRESH_PHRASES = {"phenotype": "FORCE RECOMPUTE PHENOTYPE", "standardization": "FORCE RECOMPUTE STANDARDIZATION", "provider_match": "FORCE RECOMPUTE PROVIDER MATCH"}`
  - `CACHE_FORCE_REFRESH_COOLDOWN_HOURS = 24`
  - `check_force_refresh_cooldown(cache_key, db, *, now=None) -> (allowed: bool, retry_after: str | None)`
  - `record_force_refresh(cache_key, username, db, *, now=None) -> None`

Both functions default `now` to `datetime.now()` evaluated at call time (not import time), so tests can pass a fixed `datetime` instead of monkeypatching the module. Cooldown state lives in the singleton settings document under `cacheForceRefresh.<cache_key> = {"lastRun": "<timestamp>", "byUser": "<username>"}`; a missing/never-set entry means "no cooldown in effect."

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cache_force_refresh.py
from datetime import datetime

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.cache_force_refresh import (
    CACHE_FORCE_REFRESH_PHRASES, check_force_refresh_cooldown, record_force_refresh)


def test_check_cooldown_allows_first_ever_run():
    db = FakeDatabase()
    allowed, retry_after = check_force_refresh_cooldown("phenotype", db)
    assert allowed is True
    assert retry_after is None


def test_record_then_check_blocks_within_24_hours():
    db = FakeDatabase()
    record_force_refresh("phenotype", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    allowed, retry_after = check_force_refresh_cooldown(
        "phenotype", db, now=datetime(2026, 8, 14, 11, 0),
    )
    assert allowed is False
    assert retry_after == "2026-08-15 10:00"


def test_cooldown_expires_after_24_hours():
    db = FakeDatabase()
    record_force_refresh("phenotype", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    allowed, retry_after = check_force_refresh_cooldown(
        "phenotype", db, now=datetime(2026, 8, 15, 10, 1),
    )
    assert allowed is True
    assert retry_after is None


def test_cooldowns_are_independent_per_cache():
    db = FakeDatabase()
    record_force_refresh("phenotype", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    allowed, _ = check_force_refresh_cooldown(
        "standardization", db, now=datetime(2026, 8, 14, 10, 0),
    )
    assert allowed is True


def test_record_force_refresh_stores_username_and_timestamp():
    db = FakeDatabase()
    record_force_refresh("provider_match", "admin", db, now=datetime(2026, 8, 14, 10, 0))

    from flymanager.utils.mongo.settings import get_settings
    entry = get_settings(db)["cacheForceRefresh"]["provider_match"]
    assert entry == {"lastRun": "2026-08-14 10:00", "byUser": "admin"}


def test_phrases_are_distinct_per_cache():
    assert len(set(CACHE_FORCE_REFRESH_PHRASES.values())) == len(CACHE_FORCE_REFRESH_PHRASES)
    assert set(CACHE_FORCE_REFRESH_PHRASES.keys()) == {"phenotype", "standardization", "provider_match"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_cache_force_refresh.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flymanager.utils.mongo.cache_force_refresh'`

- [ ] **Step 3: Implement the module**

```python
# flymanager/utils/mongo/cache_force_refresh.py
from datetime import datetime, timedelta

from flymanager.utils.mongo.settings import get_settings, update_settings

CACHE_FORCE_REFRESH_COOLDOWN_HOURS = 24

CACHE_FORCE_REFRESH_PHRASES = {
    "phenotype": "FORCE RECOMPUTE PHENOTYPE",
    "standardization": "FORCE RECOMPUTE STANDARDIZATION",
    "provider_match": "FORCE RECOMPUTE PROVIDER MATCH",
}

_TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")


def _parse_timestamp(text):
    normalized = str(text or "").strip()
    if not normalized:
        return None
    for timestamp_format in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(normalized, timestamp_format)
        except ValueError:
            continue
    return None


def check_force_refresh_cooldown(cache_key, db, *, now=None):
    """Returns (allowed, retry_after). retry_after is None when allowed."""
    now = now or datetime.now()
    settings = get_settings(db)
    entry = (settings.get("cacheForceRefresh") or {}).get(cache_key) or {}
    last_run = _parse_timestamp(entry.get("lastRun"))
    if last_run is None:
        return True, None

    cooldown_ends = last_run + timedelta(hours=CACHE_FORCE_REFRESH_COOLDOWN_HOURS)
    if now >= cooldown_ends:
        return True, None

    return False, cooldown_ends.strftime("%Y-%m-%d %H:%M")


def record_force_refresh(cache_key, username, db, *, now=None):
    now = now or datetime.now()
    settings = get_settings(db)
    cache_force_refresh = dict(settings.get("cacheForceRefresh") or {})
    cache_force_refresh[cache_key] = {
        "lastRun": now.strftime("%Y-%m-%d %H:%M"),
        "byUser": username,
    }
    update_settings({"cacheForceRefresh": cache_force_refresh}, db)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_cache_force_refresh.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/mongo/cache_force_refresh.py tests/test_cache_force_refresh.py
git commit -m "feat: add cache_force_refresh cooldown/phrase module"
```

---

## Task 6: `_force_recompute_cache` + `task_force_recompute_cache` background task

**Files:**
- Modify: `flymanager/app/jobs/tasks.py` (add after `_rebuild_caches_after_flybase_refresh` from Task 4)
- Test: `tests/test_force_recompute_task.py`

**Interfaces:**
- Consumes: `backfill_stock_phenotype_cache`/`backfill_cross_phenotype_cache`, `backfill_stock_standardization_cache`/`backfill_cross_standardization_cache`, `backfill_stock_provider_match_cache` (same three import paths as Task 4), `record_force_refresh` (Task 5), `write_activity` (already imported at the top of `tasks.py`).
- Produces:
  - `_force_recompute_cache(cache_key, db) -> dict` — pure dispatcher, no job-status side effects. For `cache_key == "phenotype"` or `"standardization"`, runs both the stock and cross backfill wrappers with `force=True, users=None` and returns `{"stocks": {...}, "crosses": {...}}`. For `cache_key == "provider_match"`, runs only the stock wrapper and returns `{"stocks": {...}}`. Raises `ValueError` for any other `cache_key`.
  - `task_force_recompute_cache(key, username, cache_key)` — follows the same `_run(key, work)` envelope every other task in this file uses. Calls `_force_recompute_cache`, then `record_force_refresh(cache_key, username, db)`, then `write_activity(username, f"[FORCE-RECOMPUTE] Forced full recompute of {cache_key} cache", db)`, and returns `{"message": ..., "summary": ...}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_force_recompute_task.py
from unittest.mock import ANY, patch

import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.app.jobs.tasks import _force_recompute_cache


def test_force_recompute_phenotype_calls_stock_and_cross_backfill_with_force_true():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.utils.phenotypes.backfill.backfill_stock_phenotype_cache",
        return_value={"scanned": 2, "updated": 2, "skipped_valid": 0},
    ) as stock_backfill, patch(
        "flymanager.utils.phenotypes.backfill.backfill_cross_phenotype_cache",
        return_value={"scanned": 1, "updated": 1, "skipped_valid": 0},
    ) as cross_backfill:
        summary = _force_recompute_cache("phenotype", db)

    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    cross_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    assert summary == {
        "stocks": {"scanned": 2, "updated": 2, "skipped_valid": 0},
        "crosses": {"scanned": 1, "updated": 1, "skipped_valid": 0},
    }


def test_force_recompute_standardization_calls_stock_and_cross_backfill_with_force_true():
    db = FakeDatabase({"stocks": [], "crosses": []})

    with patch(
        "flymanager.app.services.standardization_backfill.backfill_stock_standardization_cache",
        return_value={"scanned": 3, "updated": 1, "skipped_valid": 2},
    ) as stock_backfill, patch(
        "flymanager.app.services.standardization_backfill.backfill_cross_standardization_cache",
        return_value={"scanned": 0, "updated": 0, "skipped_valid": 0},
    ) as cross_backfill:
        summary = _force_recompute_cache("standardization", db)

    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    cross_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    assert summary["stocks"]["updated"] == 1


def test_force_recompute_provider_match_calls_stock_backfill_only():
    db = FakeDatabase({"stocks": []})

    with patch(
        "flymanager.app.routes.stock.backfill_stock_provider_match_cache",
        return_value={"scanned": 4, "updated": 4, "skipped_valid": 0, "errors": 0},
    ) as stock_backfill:
        summary = _force_recompute_cache("provider_match", db)

    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=True)
    assert summary == {"stocks": {"scanned": 4, "updated": 4, "skipped_valid": 0, "errors": 0}}


def test_force_recompute_unknown_cache_key_raises_value_error():
    db = FakeDatabase()
    try:
        _force_recompute_cache("nonexistent", db)
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_force_recompute_task.py -v`
Expected: FAIL — `ImportError: cannot import name '_force_recompute_cache'`

- [ ] **Step 3: Implement**

```python
# flymanager/app/jobs/tasks.py — add after _rebuild_caches_after_flybase_refresh (Task 4)
def _force_recompute_cache(cache_key, db):
    """Runs the relevant backfill wrapper(s) for cache_key at force=True.

    Unlike _rebuild_caches_after_flybase_refresh, this is a targeted dispatch
    for exactly one cache (the one an admin explicitly chose to force), not a
    fan-out across all five wrappers.
    """
    if cache_key == "phenotype":
        from flymanager.utils.phenotypes.backfill import (
            backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)
        return {
            "stocks": backfill_stock_phenotype_cache(db["stocks"], users=None, dry_run=False, force=True),
            "crosses": backfill_cross_phenotype_cache(db["crosses"], users=None, dry_run=False, force=True),
        }
    if cache_key == "standardization":
        from flymanager.app.services.standardization_backfill import (
            backfill_cross_standardization_cache, backfill_stock_standardization_cache)
        return {
            "stocks": backfill_stock_standardization_cache(db["stocks"], users=None, dry_run=False, force=True),
            "crosses": backfill_cross_standardization_cache(db["crosses"], users=None, dry_run=False, force=True),
        }
    if cache_key == "provider_match":
        from flymanager.app.routes.stock import backfill_stock_provider_match_cache
        return {
            "stocks": backfill_stock_provider_match_cache(db["stocks"], users=None, dry_run=False, force=True),
        }
    raise ValueError(f"Unknown cache_key: {cache_key}")


def task_force_recompute_cache(key, username, cache_key):
    def work(app, db):
        from flymanager.utils.mongo.cache_force_refresh import record_force_refresh

        summary = _force_recompute_cache(cache_key, db)
        record_force_refresh(cache_key, username, db)
        write_activity(
            username,
            f"[FORCE-RECOMPUTE] Forced full recompute of {cache_key} cache",
            db,
        )
        message = f"Forced full recompute of the {cache_key} cache complete: {summary}"
        return {"message": message, "summary": summary}

    _run(key, work)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_force_recompute_task.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/app/jobs/tasks.py tests/test_force_recompute_task.py
git commit -m "feat: add task_force_recompute_cache background task"
```

---

## Task 7: Guarded force-recompute route (retiring the old unconditional provider-match refresh)

**Files:**
- Modify: `flymanager/app/routes/settings.py` (imports; new route; remove `refresh_all_provider_caches`, lines 199-218)
- Modify: `flymanager/app/jobs/tasks.py` (remove `task_refresh_provider_caches`, lines 200-233 — superseded by `task_force_recompute_cache("provider_match", ...)`)
- Test: `tests/test_force_recompute_route.py`

**Interfaces:**
- Consumes: `CACHE_FORCE_REFRESH_PHRASES`, `check_force_refresh_cooldown` (Task 5), `task_force_recompute_cache` (Task 6, via `job_tasks.task_force_recompute_cache`), `enqueue_job` and `_enqueue_or_flash_conflict` (already in `settings.py`).
- Produces: `POST /settings/force-recompute-cache/<cache_key>`, form-encoded body `confirmPhrase`. `cache_key` not one of `phenotype`/`standardization`/`provider_match` → `404`. Wrong phrase → flash error, `302` redirect back, no job dispatched. Cooldown active → flash error (including the retry-after time), `302` redirect back, no job dispatched. Otherwise dispatches `task_force_recompute_cache` via the existing `_enqueue_or_flash_conflict` helper, same as every other admin bulk action in this file.

This route replaces `refresh_all_provider_caches` as the provider-match force path — `task_refresh_provider_caches` becomes dead code once nothing calls it, so this task removes both.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_force_recompute_route.py
from unittest.mock import ANY, patch

import flymanager.app  # noqa: F401


def test_force_recompute_rejects_unknown_cache_key(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        response = client.post(
            "/settings/force-recompute-cache/not-a-real-cache",
            data={"confirmPhrase": "whatever"},
        )

    assert response.status_code == 404


def test_force_recompute_rejects_wrong_confirm_phrase(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.settings.enqueue_job") as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/phenotype",
                data={"confirmPhrase": "wrong phrase"},
            )

    assert response.status_code == 302
    enqueue_job.assert_not_called()


def test_force_recompute_rejects_when_cooldown_active(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.check_force_refresh_cooldown",
            return_value=(False, "2026-08-15 10:00"),
        ) as cooldown_check, patch(
            "flymanager.app.routes.settings.enqueue_job"
        ) as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/phenotype",
                data={"confirmPhrase": "FORCE RECOMPUTE PHENOTYPE"},
            )

    assert response.status_code == 302
    cooldown_check.assert_called_once_with("phenotype", ANY)
    enqueue_job.assert_not_called()


def test_force_recompute_dispatches_job_when_confirmed_and_cooldown_clear(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.check_force_refresh_cooldown",
            return_value=(True, None),
        ), patch(
            "flymanager.app.routes.settings.enqueue_job",
            return_value="job-key-123",
        ) as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/provider_match",
                data={"confirmPhrase": "FORCE RECOMPUTE PROVIDER MATCH"},
            )

    assert response.status_code == 302
    enqueue_job.assert_called_once()
    call_kwargs = enqueue_job.call_args.kwargs
    assert call_kwargs["key"] == "maintenance:force-cache-refresh:provider_match"
    assert call_kwargs["kwargs"] == {
        "key": "maintenance:force-cache-refresh:provider_match",
        "username": "admin",
        "cache_key": "provider_match",
    }


def test_force_recompute_requires_admin_session(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "not-admin"

        with patch("flymanager.app.routes.settings.enqueue_job") as enqueue_job:
            response = client.post(
                "/settings/force-recompute-cache/phenotype",
                data={"confirmPhrase": "FORCE RECOMPUTE PHENOTYPE"},
            )

    assert response.status_code == 302
    enqueue_job.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_force_recompute_route.py -v`
Expected: FAIL — `404 NOT FOUND` for all (route doesn't exist yet).

- [ ] **Step 3: Add the import, the route, and remove the superseded route/task**

```python
# flymanager/app/routes/settings.py — extend the flask import to add abort
from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
```

```python
# flymanager/app/routes/settings.py — add to the existing import from flymanager.utils.mongo
from flymanager.utils.mongo.cache_force_refresh import (
    CACHE_FORCE_REFRESH_PHRASES, check_force_refresh_cooldown)
```

```python
# flymanager/app/routes/settings.py — replace refresh_all_provider_caches (lines 199-218)
@bp.route("/settings/force-recompute-cache/<cache_key>", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def force_recompute_cache(cache_key):
    username = session.get("username")
    redirect_to = url_for("settings.admin_settings")

    if cache_key not in CACHE_FORCE_REFRESH_PHRASES:
        abort(404)

    expected_phrase = CACHE_FORCE_REFRESH_PHRASES[cache_key]
    submitted_phrase = (request.form.get("confirmPhrase") or "").strip()
    if submitted_phrase != expected_phrase:
        flash(
            f'Confirmation phrase did not match. Type exactly: "{expected_phrase}"',
            "error",
        )
        return redirect(redirect_to)

    allowed, retry_after = check_force_refresh_cooldown(cache_key, db)
    if not allowed:
        flash(
            f"A forced full recompute of the {cache_key} cache already ran recently. "
            f"Next allowed at {retry_after}.",
            "error",
        )
        return redirect(redirect_to)

    return _enqueue_or_flash_conflict(
        redirect_to=redirect_to,
        started_message=f"Forced full recompute of the {cache_key} cache started in the background.",
        key=f"maintenance:force-cache-refresh:{cache_key}",
        actor=username,
        label=f"Force recompute {cache_key} cache",
        func=job_tasks.task_force_recompute_cache,
        task_kwargs={"username": username, "cache_key": cache_key},
        ttl_seconds=3600,
        metadata={"route": "force_recompute_cache", "cache_key": cache_key},
        conflict_message=f"A forced recompute of the {cache_key} cache is already running.",
    )
```

```python
# flymanager/app/jobs/tasks.py — delete task_refresh_provider_caches (lines 200-233) entirely;
# it is superseded by task_force_recompute_cache("provider_match", ...) from Task 6.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_force_recompute_route.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Search for and remove any other reference to the retired route/task**

Run: `grep -rn "refresh_all_provider_caches\|task_refresh_provider_caches" flymanager/ tests/`
Expected: only template references remain (`home.html`), which Task 8 replaces. Remove any stray Python references this search turns up that Task 8 doesn't already cover (there should be none besides the templates).

- [ ] **Step 6: Run the full settings/jobs test suite to confirm no regressions**

Run: `python -m pytest tests/test_flybase_admin.py tests/test_jobs_queue.py tests/test_background_jobs.py -v`
Expected: PASS, aside from any pre-existing test that specifically exercised `refresh_all_provider_caches`/`task_refresh_provider_caches` — those tests must be deleted in this step since the route/task no longer exists (search `grep -rln "refresh_all_provider_caches\|task_refresh_provider_caches" tests/` and remove the matched test functions).

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/routes/settings.py flymanager/app/jobs/tasks.py tests/test_force_recompute_route.py
git commit -m "feat: add guarded force-recompute-cache route, retire unconditional provider-cache refresh"
```

---

## Task 8: Admin UI for guarded force-recompute

**Files:**
- Modify: `flymanager/app/templates/settings/admin.html` (add after the existing "Phenotype Cache Backfill" card, around line 278)
- Modify: `flymanager/app/templates/home.html` (remove the "Refresh All Provider Caches" form, lines ~1336-1338, replacing it with a link to the admin settings page)

**Interfaces:**
- Consumes: `POST /settings/force-recompute-cache/<cache_key>` (Task 7).
- Produces: three parallel "Force Full Recompute" cards on the admin settings page (one per cache), each with a typed-confirmation-phrase text input, following the existing `data-confirm-message`/`data-progress-form`/`data-progress-message`/`data-progress-duplicate-message`/`data-progress-label` convention every other admin dispatch button in this template already uses.

This task is template-only — there's no Python test to write. Verify manually per Step 3.

- [ ] **Step 1: Add the three guarded cards to `settings/admin.html`**

```html
<!-- flymanager/app/templates/settings/admin.html — add after the existing
     "Phenotype Cache Backfill" app-action-card (around line 278) -->
            <div class="app-action-card">
                <div>
                    <h6 class="mb-1">Force Full Recompute: Phenotype Cache</h6>
                    <p class="text-muted mb-0">Rebuilds every stock and cross phenotype cache unconditionally, even entries that are already valid. Targeted rebuild (above) already covers routine edits and FlyBase refreshes automatically — use this only when the cached data itself is suspected wrong (e.g. after a bug fix to the phenotype predictor). Limited to once every 24 hours.</p>
                </div>
                <form method="POST" action="{{ url_for('settings.force_recompute_cache', cache_key='phenotype') }}" data-confirm-message="This forces a full recompute of every phenotype cache entry, even ones that are already valid. This is rarely needed and can take a long time on a large database. Continue?" data-progress-form data-progress-message="Forcing full phenotype cache recompute..." data-progress-duplicate-message="A forced phenotype cache recompute is already running." data-progress-label="Recomputing...">
                    <div class="mb-2">
                        <label class="form-label" for="phenotypeForceConfirmPhrase">Type <code>FORCE RECOMPUTE PHENOTYPE</code> to confirm</label>
                        <input type="text" class="form-control" id="phenotypeForceConfirmPhrase" name="confirmPhrase" autocomplete="off" required>
                    </div>
                    <button type="submit" class="btn btn-outline-danger" data-progress-label="Recomputing...">
                        <i class="fas fa-exclamation-triangle"></i> Force Full Recompute (Phenotype)
                    </button>
                </form>
            </div>

            <div class="app-action-card">
                <div>
                    <h6 class="mb-1">Force Full Recompute: Standardization Cache</h6>
                    <p class="text-muted mb-0">Rebuilds every stock and cross standardization cache unconditionally, even entries that are already valid. Use this only when the cached data itself is suspected wrong. Limited to once every 24 hours.</p>
                </div>
                <form method="POST" action="{{ url_for('settings.force_recompute_cache', cache_key='standardization') }}" data-confirm-message="This forces a full recompute of every standardization cache entry, even ones that are already valid. This is rarely needed and can take a long time on a large database. Continue?" data-progress-form data-progress-message="Forcing full standardization cache recompute..." data-progress-duplicate-message="A forced standardization cache recompute is already running." data-progress-label="Recomputing...">
                    <div class="mb-2">
                        <label class="form-label" for="standardizationForceConfirmPhrase">Type <code>FORCE RECOMPUTE STANDARDIZATION</code> to confirm</label>
                        <input type="text" class="form-control" id="standardizationForceConfirmPhrase" name="confirmPhrase" autocomplete="off" required>
                    </div>
                    <button type="submit" class="btn btn-outline-danger" data-progress-label="Recomputing...">
                        <i class="fas fa-exclamation-triangle"></i> Force Full Recompute (Standardization)
                    </button>
                </form>
            </div>

            <div class="app-action-card">
                <div>
                    <h6 class="mb-1">Force Full Recompute: Provider Match Cache</h6>
                    <p class="text-muted mb-0">Rebuilds every stock's provider match cache unconditionally, even entries that are already valid. Targeted rebuild already covers routine edits and FlyBase refreshes automatically. Limited to once every 24 hours.</p>
                </div>
                <form method="POST" action="{{ url_for('settings.force_recompute_cache', cache_key='provider_match') }}" data-confirm-message="This forces a full recompute of every provider match cache entry, even ones that are already valid. This is rarely needed and can take a long time on a large database. Continue?" data-progress-form data-progress-message="Forcing full provider match cache recompute..." data-progress-duplicate-message="A forced provider match cache recompute is already running." data-progress-label="Recomputing...">
                    <div class="mb-2">
                        <label class="form-label" for="providerMatchForceConfirmPhrase">Type <code>FORCE RECOMPUTE PROVIDER MATCH</code> to confirm</label>
                        <input type="text" class="form-control" id="providerMatchForceConfirmPhrase" name="confirmPhrase" autocomplete="off" required>
                    </div>
                    <button type="submit" class="btn btn-outline-danger" data-progress-label="Recomputing...">
                        <i class="fas fa-exclamation-triangle"></i> Force Full Recompute (Provider Match)
                    </button>
                </form>
            </div>
```

- [ ] **Step 2: Remove the retired "Refresh All Provider Caches" button from `home.html`**

```html
<!-- flymanager/app/templates/home.html — remove this form (around lines 1336-1338),
     leaving the phenotype-backfill form and the "Open Admin Settings" link in place -->
                    <form method="POST" action="{{ url_for('settings.refresh_all_provider_caches') }}" class="mb-0" data-confirm-message="This will rerun provider match caching for every stock in the database. Continue?" data-progress-form data-progress-message="Refreshing provider caches for all stocks..." data-progress-duplicate-message="A global provider cache refresh is already running." data-progress-label="Refreshing...">
                        <button type="submit" class="btn btn-outline-primary btn-sm" data-progress-label="Refreshing...">Refresh All Provider Caches</button>
                    </form>
```

The remaining "Open Admin Settings" link already routes to the page where the three guarded force-recompute cards (Step 1) now live, so no replacement link is needed on the dashboard.

- [ ] **Step 3: Manually verify in the running app**

Use the `run-flymanager` skill to start the app, then:
1. Log in as an admin user and open Admin Settings.
2. Confirm the three new "Force Full Recompute" cards render, each with its own confirmation-phrase input.
3. Submit one with an empty/wrong phrase — confirm it's rejected (flash message showing the exact expected phrase) and no job starts.
4. Submit one with the correct phrase — confirm the JS confirm dialog appears, then the job starts (progress banner shows) and completes.
5. Immediately submit the same card again with the correct phrase — confirm it's rejected with a "next allowed at ..." message (cooldown).
6. Edit a stock's genotype for a stock that is a parent of an existing cross (create one first if needed) — confirm the cross's `PhenotypeCache`/`StandardizationCache` and denormalized genotype field update without needing to open/edit the cross directly.
7. Confirm the home dashboard's admin card no longer shows a standalone "Refresh All Provider Caches" button.

- [ ] **Step 4: Commit**

```bash
git add flymanager/app/templates/settings/admin.html flymanager/app/templates/home.html
git commit -m "feat: add guarded force-recompute UI, retire standalone provider-cache-refresh button"
```
