# Provider Match Cache Unification & Apply-to-Record Design

**Status:** Approved for planning.
**Depends on:** `docs/superpowers/plans/2026-08-14-mongodb-efficiency.md` (this work should be sequenced after that plan — it reuses the shared `tests/mongo_fakes.py` test-double module from that plan's Task 1, and touches some of the same files (`flymanager/app/routes/stock.py`, `flymanager/app/jobs/tasks.py`) that the efficiency plan also modifies, so running efficiency-plan-first avoids merge churn).

## Goal

FlyManager has three materialized, document-embedded derived caches: `PhenotypeCache`, `StandardizationCache`, and `ProviderMatchCache`. The first two already share a generic backfill engine (`flymanager/utils/materialized_cache.py`) and a common versioned/signature envelope. `ProviderMatchCache` is a bespoke one-off with its own hand-rolled bulk job, a weaker invalidation check, and — uniquely among the three — no way for a user to promote a cached candidate into the authoritative stock record. This design:

1. Migrates `ProviderMatchCache` onto the shared `materialized_cache.py` engine, fixing a real invalidation gap in the process.
2. Adds a new, generic "apply a cache candidate into the record" capability, and wires it up for provider matches (the only cache system that currently lacks any such affordance, even client-side).

## Background (verified against current code)

- `flymanager/utils/materialized_cache.py:59` — `backfill_materialized_cache(collection, *, cache_field, cache_getter, cache_builder, ...)` is the shared engine. Both phenotype (`flymanager/utils/phenotypes/backfill.py:16-49`) and standardization (`flymanager/app/services/standardization_backfill.py`) backfills are thin wrappers over it.
- Phenotype/standardization cache envelopes both carry `version` and `pipelineSignature` (from `compute_flybase_pipeline_signature()`, `flymanager/utils/phenotypes/predictor.py`), so a cache entry is invalidated both when the record's own genotype changes *and* when the underlying FlyBase reference data is re-ingested.
- `ProviderMatchCache` (`PROVIDER_MATCH_CACHE_FIELD`, `flymanager/app/routes/stock.py:51`) instead stores `{signature, candidates, count, cachedAt}` (`_store_provider_match_cache`, `stock.py:505-521`), where `signature` (`_build_provider_match_cache_signature`, `stock.py:335-350`) is a hash of only the stock's own fields (`SourceID`, `Genotype`, `FlyBaseStockID`, etc.) — **it never changes when FlyBase reference data is re-ingested**, so a cache entry can go stale (miss newly-indexed candidate stocks) indefinitely until a user hits `?refresh=1` or the nightly bulk job forces it. This is a real gap, not just an inconsistency — phenotype/standardization don't have it because they check `pipelineSignature` too.
- The bulk refresh job (`task_refresh_provider_caches`, `flymanager/app/jobs/tasks.py:200-233`) is a hand-rolled loop over `db["stocks"].find(...)`, not built on `backfill_materialized_cache`. It wraps each record's rebuild in `try/except` and counts errors (`stock.py`'s `_get_provider_match_payload` can raise if, e.g., the Bloomington reference dataframe is unavailable for one stock) — `backfill_materialized_cache` has no equivalent per-record error handling today, so a naive migration would let one bad record abort the whole bulk run.
- `ProviderMatchCache` is stock-only. There is no cross equivalent (confirmed: no `ProviderMatch`/`reverse_search` references in `flymanager/app/routes/cross.py`), so this design touches stocks only.
- No cache in the app currently has a server-side "promote candidate into record" endpoint. The closest precedent is standardization's client-side-only `applyStandardizationReplacement` (`flymanager/app/templates/stock/view_stock.html:1116-1131`), which patches the open Tagify genotype editor in the DOM and requires a manual save — it does not write anything itself. Provider-match candidates today are rendered read-only (`renderProviderMatches`, `view_stock.html:1018-1065`); the only actionable element per candidate is an "Open" link to the provider's page.
- The generic authoritative-write path already exists and is what every normal edit goes through: `apply_updates_to_owned_document` / `build_owned_document_update_fields` (`flymanager/utils/mongo_records.py:282-329`). It computes a `$set`, appends human-readable entries to `ModificationLog`, and bumps `DataModifiedDate` — i.e. applying a match this way is automatically visible in the record's edit history, with no new audit mechanism needed.
- Candidate object shape (`find_external_stock_matches`, `flymanager/utils/stock_sources.py:265-327`, and `_build_legacy_bloomington_stock_payload`, `stock_sources.py:379-404`): `stockSource`, `sourceCollection`, `sourceID`, `flyBaseStockID`, `name`, `supportStatus`, `matchScore`, `matchReasons`, `providerURL`.
- Corresponding stock record fields (`flymanager/app/routes/stock.py:991-999`, `flymanager/utils/mongo/stocks.py:65`): `StockSource`, `SourceCollection`, `SourceID`, `FlyBaseStockID`, `ExternalSupportStatus`.

## Decisions (confirmed with user)

- Both problems in scope, unified: migrate the cache engine *and* build the apply capability in the same effort.
- The apply capability is built generically (reusable field-mapping diff + write helper) but only wired up for provider-match in this pass. Standardization's existing client-side replace flow is **not** migrated onto it — its semantics (replacing a token inside genotype text) differ enough from discrete field promotion that generalizing it now would be speculative (YAGNI).
- Overwrite policy: when applying a candidate would change a field that already has a different manually-entered value, the UI shows a diff/confirmation ("this will replace X with Y") before applying. No separate approval workflow — a single confirm-and-apply action.

## Architecture

### Part 1 — Migrate `ProviderMatchCache` onto `materialized_cache.py`

1. **Extend `backfill_materialized_cache` with per-record error containment.** Add a `try/except` around the `cache_builder(record)` call; on exception, log it, increment a new `errors` count in the returned summary, and skip that record (no `$set`) rather than aborting the run. This is a pure additive change — existing callers (phenotype, standardization) don't raise today, so their behavior and summaries are unchanged except for the new `errors: 0` key.
2. **New envelope for `ProviderMatchCache`:** add `version` (start at `1`) and `pipelineSignature` (`compute_flybase_pipeline_signature()`) alongside the existing `signature`, `candidates`, `count`, `cachedAt`. Validity now requires all three: `version` matches, `pipelineSignature` matches current FlyBase data, and the existing per-record `signature` matches. This closes the stale-on-reference-update gap described above.
3. **`cache_getter`/`cache_builder` wrappers** in `flymanager/app/routes/stock.py` (or extracted alongside the existing provider-match helpers) adapt the current `_get_valid_provider_match_cache` / `_store_provider_match_cache` logic to the `(record) -> cache-or-None` / `(record) -> cache_payload` shapes `backfill_materialized_cache` expects. `_get_provider_match_payload`'s on-demand (non-bulk) path keeps using these same helpers directly, so single-record refresh (`GET /reverse_search/<uid>`) and bulk refresh compute identically.
4. **`task_refresh_provider_caches`** (`jobs/tasks.py:200-233`) is rewritten to call `backfill_materialized_cache` the same way `task_backfill_phenotype_cache` does, instead of its hand-rolled loop. The summary message format is preserved (scanned/refreshed/errors/candidate counts) by reading the new `errors` field the engine now returns instead of counting them locally.

### Part 2 — Generic "apply candidate → record" capability

1. **New helper**, colocated with `apply_updates_to_owned_document` in `flymanager/utils/mongo_records.py` (same module, since it's a thin wrapper over the existing write path, not a new subsystem):

   ```python
   def diff_candidate_against_record(current_document, field_mapping):
       """field_mapping: {record_field: candidate_value}.
       Returns {field: {"current": ..., "candidate": ...}} only for fields
       that would actually change (candidate value differs from current,
       treating "" / None as equivalent-empty)."""

   def apply_candidate_to_owned_document(collection_name, user, uid, db, field_mapping, *, source_label):
       """Writes field_mapping via apply_updates_to_owned_document (inheriting
       its ModificationLog/DataModifiedDate bookkeeping), tagging the log
       entries with source_label (e.g. 'Applied BDSC_60590 provider match') so
       history reads clearly."""
   ```

   `field_mapping` is caller-supplied, so this is not provider-match-specific — any future "promote a cache candidate into the record" feature (e.g. a future standardization redesign) can reuse `diff_candidate_against_record` + `apply_candidate_to_owned_document` without new plumbing.

2. **New route** `POST /stock/<unique_id>/apply_provider_match` in `flymanager/app/routes/stock.py`. Request carries a candidate identifier (index into the cached `candidates` list, validated against the current cache — not client-trusted raw field values, so a stale/tampered request can't write arbitrary data). Behavior:
   - Look up the candidate in the stock's current `ProviderMatchCache` (refreshing first if the cache is invalid, same as the reverse-search route does).
   - Build `field_mapping = {"StockSource": candidate["stockSource"], "SourceCollection": candidate["sourceCollection"], "SourceID": candidate["sourceID"], "FlyBaseStockID": candidate["flyBaseStockID"], "ExternalSupportStatus": candidate["supportStatus"]}`.
   - Call `diff_candidate_against_record`. If the request doesn't carry `confirm=1` and the diff is non-empty *and conflicting* (i.e. changes a field that already had a different non-empty value), return the diff as JSON for the frontend to render as a confirmation dialog instead of applying.
   - Otherwise (no conflicts, or `confirm=1` supplied) call `apply_candidate_to_owned_document` and return the updated field values so the page can refresh without a full reload.
   - Fields that are already blank get filled without needing confirmation — only genuinely conflicting fields trigger the warn step, matching "warn, then overwrite on confirm" without making every apply, even a harmless fill-in-the-blanks one, require a click-through.

3. **Frontend** (`flymanager/app/templates/stock/view_stock.html`, `renderProviderMatches`): add an "Apply" button next to each candidate's existing "Open" link. Clicking it calls the new route; if the response is a conflict diff, show a small inline confirmation ("This will change Support Status from `manual` to `supported` — Apply anyway?") before re-submitting with `confirm=1`. On success, update the "Ordering Metadata" panel's Source/Source ID/FlyBase Stock ID/Support Status boxes in place and show a brief success note, consistent with how `applyStandardizationReplacement` gives inline feedback today.

## Data model changes

- `ProviderMatchCache` gains two fields: `version` (int), `pipelineSignature` (string). No migration needed — an old-shaped cache entry simply fails the new validity check once and gets rebuilt on next access, same as any other cache miss.
- No new collections. No schema change to the stock document beyond the five fields the apply action can already legally write today via manual edit (`StockSource`, `SourceCollection`, `SourceID`, `FlyBaseStockID`, `ExternalSupportStatus`).

## Error handling

- Bulk backfill: a single record's `cache_builder` exception (e.g. missing Bloomington reference data) is caught, logged, and counted — it no longer aborts the run (this is the `materialized_cache.py` change in Part 1, Step 1).
- Apply route: candidate index out of range or cache invalid at apply time → 409 with a "matches changed, please refresh and retry" message rather than silently applying stale data.
- Apply route never trusts client-supplied field values directly — it always re-derives `field_mapping` from the server-held cached candidate by index, so a manipulated request body can't inject arbitrary field writes.

## Testing strategy

Follows the same TDD/`FakeDatabase` pattern as the mongodb-efficiency plan (reusing `tests/mongo_fakes.py`):
- `materialized_cache.py`'s new error-containment path: a `cache_builder` that raises for one record, verify it's skipped and counted while other records still update.
- Provider-match cache: version/pipelineSignature invalidation (stale pipeline signature triggers rebuild even when the per-record signature is unchanged).
- `diff_candidate_against_record`: empty-vs-empty is not a conflict; differing non-empty values are; identical values produce no diff entry.
- `apply_candidate_to_owned_document`: writes the mapped fields, appends a `ModificationLog` entry, and bumps `DataModifiedDate`, via the existing `apply_updates_to_owned_document` path (assert by calling it, not by re-testing that path's internals).
- Route-level test for `/stock/<uid>/apply_provider_match`: no-conflict apply succeeds without `confirm`; conflicting apply without `confirm` returns the diff and does not write; conflicting apply with `confirm=1` writes.

## Out of scope

- Any change to `StandardizationCache`'s existing client-side replace flow.
- Extending provider-match (or the apply capability) to crosses — no cross equivalent exists today and none was requested.
- A general-purpose audit/history UI — `ModificationLog` already covers this and is unchanged.
