# Cache Invalidation: Partial Recalculation & Guarded Full Recompute Design

**Status:** Approved for planning.
**Depends on:** `docs/superpowers/plans/2026-08-14-provider-match-cache-unification.md`. That plan puts `ProviderMatchCache` onto the shared `materialized_cache.py` backfill engine and gives it a `version`/`pipelineSignature` envelope matching `PhenotypeCache`/`StandardizationCache`. This design assumes all three caches already share that envelope shape and the `backfill_materialized_cache(..., force=...)` engine; it does not re-derive anything the unification plan builds. Do not start until that plan's tasks are complete.

## Goal

FlyManager's three materialized caches (`PhenotypeCache`, `StandardizationCache`, `ProviderMatchCache`) already invalidate correctly for the record being edited — `edit_stock`/`edit_cross` rebuild a record's own cache when its genotype changes, and `backfill_materialized_cache(force=False)` already skips any record whose signature still validates. What's missing is **propagation** and **guardrails**:

1. Editing a stock's genotype does not refresh the caches of crosses that reference it as a parent — those caches go silently stale until someone edits the cross directly (which may never happen).
2. Re-ingesting FlyBase reference data changes the global `pipelineSignature` but triggers no rebuild of anything — staleness is only discovered lazily, or an admin has to remember to click a separate bulk-backfill button.
3. The one existing "recompute everything" action (`task_refresh_provider_caches`) forces a full recompute of every stock unconditionally, with only a JS `confirm()` dialog standing between an admin and DB-wide recompute — and there's no equivalent force option, or guardrail pattern, for phenotype/standardization.

This design adds: (a) eager cross-record propagation on stock edit, (b) an auto-triggered targeted rebuild after reference-data refresh, and (c) a single guarded "force full recompute" capability shared by all three caches, so genuine full recomputation is rare, deliberate, and audited.

## Background (verified against current code)

- `backfill_materialized_cache` (`flymanager/utils/materialized_cache.py:63-113`) already skips any record whose `cache_getter` returns truthy unless `force=True` — i.e. targeted/partial recalculation across a whole collection is already the engine's default behavior. Nothing new is needed here.
- `edit_stock` (`flymanager/utils/mongo/stocks.py:179-223`) rebuilds the stock's own `PhenotypeCache`/`StandardizationCache` when `"Genotype" in prepared_updates` (line 203), via `build_stock_phenotype_cache`/`build_stock_standardization_cache` (lines 204-209), then persists via `apply_updates_to_owned_document` (line 211). It touches no other collection.
- `edit_cross` (`flymanager/utils/mongo/crosses.py:182-240`) rebuilds its own caches when the update contains `MaleGenotype`/`FemaleGenotype` (line 206), falling back to the current document's stored genotype for whichever side isn't in the update (lines 211-218). Crosses store `MaleUniqueID`/`FemaleUniqueID` (lines 78-79) as a link to parent stocks, and `MaleGenotype`/`FemaleGenotype` as denormalized copies captured at cross create/edit time — nothing keeps those copies in sync with the parent stock after the fact.
- No Mongo index exists on `crosses.MaleUniqueID` or `crosses.FemaleUniqueID` (`flymanager/utils/mongo/db.py:51-73`, `ensure_mongo_indexes` — confirmed, only `(User, UniqueID)`/`(AssignedTo, UniqueID)`/status/tray compounds exist for stocks/crosses).
- `task_refresh_flybase_reference_data` (`flymanager/app/jobs/tasks.py:266-283`) calls `flybase_service.manual_refresh_flybase_reference_data(app)` inside a `work(app, db)` closure run via the existing `_run(key, work)` helper (`tasks.py:20-34`), and returns a result dict stored by `mark_job_succeeded`. No task currently chains into another task's logic — this design adds the first instance, by calling the backfill wrapper functions directly (not by invoking another task), inside the same closure.
- `task_backfill_phenotype_cache` (`tasks.py:155-197`) already calls its four backfill wrappers with `force=False` (targeted) — phenotype/standardization currently have **no** admin-facing force=True option at all. `task_refresh_provider_caches` (`tasks.py:200-233`, being rewritten by the unification plan's Task 3 to call `backfill_stock_provider_match_cache(db["stocks"], force=True)`) is the only cache with an unconditional-force path today, and it has no cooldown or typed confirmation — just a `data-confirm-message` JS dialog on the admin form.
- A singleton app-wide `settings` collection already exists (`flymanager/utils/mongo/settings.py`, `get_settings`/`update_settings` operate on `db['settings'].find_one({})` / `update_one({}, ..., upsert=True)`) — suitable for storing a per-cache last-force-run timestamp without a new collection.
- `write_activity(user, activity, db)` (`flymanager/utils/mongo/activity.py:3-25`) stores only `user`/`timestamp`/free-text `activity`; there is no `action`/`category` field, so distinguishing force-recompute events from ordinary activity means encoding it into the text (a fixed prefix), not a schema change.
- Flask-Limiter (`limiter.limit(...)`) is already used on other admin/mutating routes (e.g. `flymanager/app/routes/data.py:39,93`) for per-request/per-IP throttling, but nothing in the codebase implements a per-N-hours cooldown backed by stored state — that logic is new.

## Decisions (confirmed with user)

- All three propagation/guardrail problems are in scope together (cross-record propagation, reference-data-triggered targeted rebuild, guarded force-full-recompute) — not a partial slice.
- Cross-record propagation on stock edit is **eager and synchronous**: when a stock's genotype changes, dependent crosses' denormalized genotype fields and caches are rebuilt in the same request, not via a background sweep or read-time lazy check.
- Reference-data-triggered rebuild treats every record as a candidate but relies on the existing `force=False` skip-if-valid behavior for cheapness — no new symbol-level diffing or reverse index of which records reference which changed data.
- The targeted rebuild after reference-data refresh is auto-triggered as part of the same background job, not a separate manual step.
- Force-full-recompute becomes a single capability shared by all three caches (phenotype, standardization, provider-match), replacing provider-match's existing unconditional-force loop and adding an equivalent (previously nonexistent) escape hatch for phenotype/standardization.
- The guardrail on force-full-recompute is a hard guardrail, not just clearer copy: a typed confirmation phrase, a 24-hour cooldown per cache, and an audit trail — all three together.
- The cooldown window is 24 hours per cache, tracked independently (forcing phenotype doesn't block forcing standardization or provider-match).
- The audit trail reuses the existing `write_activity` log with a distinguishing text prefix, rather than a new collection.

## Architecture

### Part 1 — Eager cross-record propagation on stock edit

1. **New index**: add `crosses.{User: 1, MaleUniqueID: 1}` and `crosses.{User: 1, FemaleUniqueID: 1}` to `ensure_mongo_indexes` (`flymanager/utils/mongo/db.py`), so the lookup added below stays cheap regardless of how many crosses reference a given stock.
2. **New helper** in `flymanager/utils/mongo/crosses.py`, e.g. `propagate_stock_genotype_to_crosses(user, stock_unique_id, new_genotype, db)`:
   - Queries `db["crosses"]` for `{"User": user, "$or": [{"MaleUniqueID": stock_unique_id}, {"FemaleUniqueID": stock_unique_id}]}`.
   - For each match, sets `MaleGenotype`/`FemaleGenotype` (whichever side matched) to `new_genotype`, rebuilds that cross's `PhenotypeCache`/`StandardizationCache` via the same builder functions `edit_cross` already calls internally (`build_cross_phenotype_cache`/`build_cross_standardization_cache`), and persists via `apply_updates_to_owned_document` directly — not by calling the full `edit_cross` function, since that also handles vial-refresh and other update-path side effects that don't apply here and would risk incidental behavior changes.
   - Returns a small summary (`{"crosses_updated": N}`) for logging/testing purposes.
3. **Wire into `edit_stock`** (`flymanager/utils/mongo/stocks.py`): immediately after the existing `"Genotype" in prepared_updates` cache rebuild (current lines 203-209), call `propagate_stock_genotype_to_crosses(user, uid, prepared_updates["Genotype"], db)`. This only fires when the stock's own genotype actually changed — same trigger condition already used for the stock's own cache rebuild, so no new signal is needed.

### Part 2 — Auto-triggered targeted rebuild after reference-data refresh

1. **Extend `task_refresh_flybase_reference_data`'s `work` closure** (`flymanager/app/jobs/tasks.py:266-283`): after `manual_refresh_flybase_reference_data(app)` succeeds, call the five existing backfill wrappers in-process, all with `force=False`:
   - `backfill_stock_phenotype_cache`, `backfill_cross_phenotype_cache` (phenotype)
   - `backfill_stock_standardization_cache`, `backfill_cross_standardization_cache` (standardization)
   - `backfill_stock_provider_match_cache` (provider-match, from the unification plan)
2. Fold each wrapper's summary dict into the job's returned result (e.g. `result["cacheRebuild"] = {"phenotype": {...}, "standardization": {...}, "providerMatch": {...}}`), so the existing job-status UI (which already renders `task_refresh_flybase_reference_data`'s result message) can show what got touched.
3. No new job, no new route, no chaining between separate background jobs — this is purely making one job's closure do more work, using the same `_run(key, work)` pattern every other task in the file already follows. Because `force=False` skips any record whose `pipelineSignature` still matches, the added cost is proportional to how much reference data actually changed, not to collection size — a no-op re-ingestion (unchanged fingerprints) costs one full collection scan per cache with zero rebuilds.

### Part 3 — Guarded force-full-recompute (shared by all three caches)

1. **Settings schema addition** (`flymanager/utils/mongo/settings.py`): the singleton settings document gains a `cacheForceRefresh` sub-object, e.g.:
   ```
   {"cacheForceRefresh": {
       "phenotype":       {"lastRun": "<timestamp>", "byUser": "<username>"},
       "standardization": {"lastRun": "<timestamp>", "byUser": "<username>"},
       "provider_match":  {"lastRun": "<timestamp>", "byUser": "<username>"}
   }}
   ```
   Missing/absent means "never run" (no cooldown blocks a first-ever force run).
2. **New helper**, e.g. `flymanager/utils/mongo/cache_force_refresh.py`:
   - `check_force_refresh_cooldown(cache_key, db) -> (allowed: bool, retry_after: str | None)` — reads `cacheForceRefresh.<cache_key>.lastRun` from settings, compares against a 24-hour window using the existing timestamp format/parsing already used elsewhere in the cache envelopes (`current_timestamp()`/`_parse_provider_match_cache_timestamp`-style helpers in `mongo_records.py`), returns whether a new force run is allowed and, if not, when it will be.
   - `record_force_refresh(cache_key, username, db)` — updates `cacheForceRefresh.<cache_key>` with the current timestamp and username.
   - `CACHE_FORCE_REFRESH_PHRASES = {"phenotype": "FORCE RECOMPUTE PHENOTYPE", "standardization": "FORCE RECOMPUTE STANDARDIZATION", "provider_match": "FORCE RECOMPUTE PROVIDER MATCH"}` (or equivalent) — the exact phrase the caller must submit, distinct per cache so an admin can't fat-finger the wrong one into submitting.
3. **New unified admin route**, e.g. `POST /settings/force-recompute-cache/<cache_key>` in `flymanager/app/routes/settings.py`:
   - Body: `{"confirmPhrase": "<string>"}`.
   - Validates `cache_key` is one of the three known keys → 404 otherwise.
   - Validates `confirmPhrase` matches the required phrase for that cache → 400 with the expected phrase on mismatch (so the UI can show it, not just reject blindly).
   - Calls `check_force_refresh_cooldown`; if not allowed → 429 with the retry-after time.
   - Dispatches a background job (reusing the existing `start_background_job`/`_run` pattern) that runs the relevant `force=True` backfill(s) for that cache (stock+cross for phenotype/standardization; stock-only for provider-match), then calls `record_force_refresh` and `write_activity(username, f"[FORCE-RECOMPUTE] Forced full recompute of {cache_key} cache: {summary}", db)`.
   - This route **replaces** the unification plan's `POST /settings/refresh-all-provider-caches` as the provider-match force path (that plan's Task 3 wrapper `backfill_stock_provider_match_cache(force=True)` becomes the thing this route's job dispatch calls, not a separately-triggerable action) and adds equivalent phenotype/standardization force actions that don't exist today.
4. **Admin UI** (`flymanager/app/templates/settings/admin.html`): replace the existing single "Refresh all provider caches" button/form with three parallel sections (one per cache), each with a short paragraph explaining that targeted rebuild already covers routine edits and reference-data refreshes, a text input for the confirmation phrase, and a submit button that's disabled (with an inline cooldown message, e.g. "Next available at 2026-08-15 14:32") when the cooldown blocks it — computed server-side and passed into the template so the disabled state is correct on page load, not just after a failed submit.

## Data model changes

- `crosses` collection gains two new indexes (`{User, MaleUniqueID}`, `{User, FemaleUniqueID}`); no document shape change.
- Singleton `settings` document gains a `cacheForceRefresh` sub-object (three keys, each `{lastRun, byUser}`); backward compatible, absent-means-never-run.
- No changes to `PhenotypeCache`/`StandardizationCache`/`ProviderMatchCache` envelope shapes — this design only changes *when* and *how broadly* existing rebuild logic runs, not the cache payloads themselves.

## Error handling

- Propagation helper: if a dependent cross's cache rebuild raises (e.g. malformed genotype), it should not abort the stock edit itself or block updating other dependent crosses — wrap each cross's rebuild in the same per-record `try/except` pattern `materialized_cache.py` already uses for bulk builders, and surface a count of any propagation failures back to the caller (e.g. logged, not raised) rather than failing the stock save.
- Reference-data-triggered rebuild: each of the five backfill wrapper calls already contains its own per-record error containment (from the unification plan's Task 1); a wrapper-level exception (e.g. the whole cache type is unavailable) should not prevent the other four from running — wrap each wrapper call in its own `try/except` inside the job closure and report which ones failed in the result.
- Force-recompute route: confirm-phrase mismatch → 400 with the expected phrase; cooldown active → 429 with retry-after; unknown `cache_key` → 404. A concurrent force-recompute request for the same cache while one is already running should be rejected via the existing `operation_locks`/`start_background_job` mechanism (already used for other long-running admin jobs), not by the cooldown alone (cooldown is 24h-scoped, not a mutex).

## Testing strategy

Follows the same TDD/`FakeDatabase` pattern as the provider-match-cache-unification plan (`tests/mongo_fakes.py`):
- `propagate_stock_genotype_to_crosses`: a stock referenced by two crosses (one as Male, one as Female) — editing its genotype updates both crosses' denormalized fields and caches; a stock referenced by zero crosses is a no-op; a raising rebuild on one cross doesn't prevent the other from updating.
- `edit_stock` integration: editing `Genotype` on a stock with dependent crosses calls the propagation helper with the right arguments (mock/patch boundary, consistent with how `edit_stock`'s existing cache-rebuild call is likely already tested).
- Reference-data job: mock `manual_refresh_flybase_reference_data` and the five backfill wrappers; assert all five are called with `force=False` and their summaries appear in the job result; assert one wrapper raising doesn't prevent the others from being called.
- Cooldown helper: no prior run → allowed; run 1 hour ago → blocked with correct retry-after; run 25 hours ago → allowed again.
- Force-recompute route: wrong phrase → 400 and no backfill call; right phrase within cooldown → 429 and no backfill call; right phrase outside cooldown → 200, backfill dispatched with `force=True`, settings/activity updated.
- Index creation: assert the new `crosses` indexes appear in `ensure_mongo_indexes`'s output (consistent with how existing index tests, if any, verify index presence).

## Out of scope

- Propagating stock edits into `ProviderMatchCache` of *other* records — provider-match candidates reference external/FlyBase data, not other FlyManager stock/cross documents, so there is no in-DB dependency to propagate (confirmed in the unification plan's background section).
- Symbol-level diffing of FlyBase reference data to compute a precise affected-record set — explicitly deferred per the "treat every record as a candidate, skip cheaply" decision; the existing signature-based skip already keeps this reasonably cheap, and building a reverse index is speculative complexity without evidence the scan cost is a real problem.
- Any change to how an individual record's own cache is invalidated on its own edit — that mechanism (signature/pipelineSignature checks) is unchanged; this design only adds propagation to *other* records and guardrails around *bulk* force recompute.
- Rate-limiting or cooldowns on the routine `force=False` backfill paths (per-record edits, the auto-triggered reference-data rebuild) — only the `force=True` full-recompute path gets the cooldown/phrase/audit guardrail, since targeted rebuild is meant to be cheap and frequent by design.
