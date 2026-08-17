# Reviewer Apply & Per-User Canonical Registry Design

**Status:** Approved for planning.

## Goal

The Stock and Cross Parent Reviewer (`main.standardization_reviewer`) is currently a read-only report. It tells you which genotype tokens need review and, sometimes, what they should become — but the only action it offers is a link out to the record page, where a separate client-side flow patches the genotype editor and asks you to save. Tokens with no suggestion are a dead end. Clean records are paginated alongside flagged ones, diluting the queue.

This design makes the reviewer a working queue:

1. **Apply suggestions in place.** Expand a row, choose a canonical candidate per flagged token, tick which tokens to accept, see the resulting genotype, and write it to the record from the reviewer.
2. **Resolve tokens that have no suggestion.** A per-user canonical registry lets you declare a token legitimate (optionally mapping it to a canonical form, optionally describing it as a visible marker), which silences it across your whole queue. The registry is exportable and importable.
3. **Show only flagged targets.** Clean rows are never built.

## Background (verified against current code)

- **The reviewer page.** `standardization_reviewer` (`flymanager/app/routes/main.py:1042`) loads all accessible stocks and crosses, builds one row per genotype-bearing subject via `_build_standardization_reviewer_rows` (`main.py:280`), paginates, and renders `stock/standardization_overview.html`. Rows are built for clean and flagged targets alike and sorted flagged-first. The template renders six columns; the only interactive element per row is an `Open Stock` / `Open Cross` anchor (`standardization_overview.html:104`).
- **The materialized cache.** `StandardizationCache` (`flymanager/app/services/stock_standardization.py:492-555`) is a versioned, `pipelineSignature`-stamped envelope embedded on each stock/cross document, built by `summarize_genotype_standardization` with `candidate_limit=0` so the bulk path never runs the expensive fuzzy candidate scan. It is deliberately lossy: it keeps `topTokens` (first four) and `recommendedReplacements` (first three), which is enough to render today's summary and **not** enough to drive an overlay or an apply flow.
- **Staleness and backfill.** `_standardization_cache_is_current` (`stock_standardization.py:558`) treats a `version` mismatch or a changed `pipelineSignature` as stale under `strict=True`; the reviewer read path uses `strict=False` so viewing never triggers a recompute. `standardization_backfill.py` wraps the shared `backfill_materialized_cache` engine, so a version bump is handled by an existing backfill run rather than new migration code.
- **The review engine.** `review_stock_standardization` (`stock_standardization.py:390`) walks genotype packages, classifies each raw token, and returns per-token issues with a `recommended_replacement` (from `REVIEWED_MARKER_ALIASES` or the FlyBase alias index) plus ranked `fuzzy_candidates`. Its notion of "known" comes from two module-level dicts in `flymanager/utils/phenotypes/visual_markers.py`: `VISUAL_MARKER_DICTIONARY` (gene stems) and `ALLELE_VISUAL_MARKER_DICTIONARY` (allele tokens). **There is no database-backed store of canonical tokens anywhere in the app** — the collections in use are stocks, crosses, users, trays, activity, settings, operation_locks, password_reset_tokens, and the `flybase_*` reference collections.
- **Tokenization.** Every flagged token is exactly one entry of `tokenize_gene_package` (`flymanager/utils/phenotypes/parser.py:17`), which splits a package on top-level `,` and ` ` with a bracket-depth stack, then `.strip()`s and `.rstrip(":")`s each token. Packages themselves come from `_iter_genotype_packages` (`stock_standardization.py:82`), which splits the genotype on top-level `;` then `/` using the same bracket-aware technique.
- **Existing apply flow (client-side only).** `applyStandardizationReplacement` (`flymanager/app/templates/stock/view_stock.html:788`) calls `replaceTokenInInstance` (`view_stock.html:759`) to swap whole Tagify tag values in the open genotype editor and then tells you to save. It writes nothing itself, exists only for stocks, and has no cross equivalent. Candidate rendering (`view_stock.html:944`) already shows source, match score, and match basis per candidate — the reviewer's expanded panel should look familiar.
- **Detail endpoint precedent.** `stock.review_stock_standardization_route` (`flymanager/app/routes/stock.py:1648`) returns a full review payload with candidates for one stock, accepting `token`/`query` args to override the fuzzy search term. It is stock-only and reports `reviewable` from `ViewerCanEdit`.
- **Permissions.** `ViewerCanEdit` is annotated as `owner == viewer` (`flymanager/utils/mongo/access.py:43`). Maintainers of an incoming assignment can view but not edit. Accessible-record fetches are `get_accessible_stock` / `get_accessible_cross` (`access.py:217`, `access.py:228`).
- **Write path.** `edit_stock` (`flymanager/utils/mongo/stocks.py:183`) and `edit_cross` (`flymanager/utils/mongo/crosses.py:182`) both take `(user, uid, db, updates, log_activity=True, refresh_vials=True)` and route through `apply_updates_to_owned_document` (`flymanager/utils/mongo_records.py:313`), which appends human-readable `ModificationLog` entries and bumps `DataModifiedDate`. Applying a replacement through these helpers therefore lands in the record's edit history with no new audit mechanism.
- **Settings.** `settings/admin.html` is admin-gated (`flymanager/app/routes/settings.py:48-126`), so a per-user registry management UI cannot live there.
- **No file store.** `UPLOAD_FOLDER` (`flymanager/app/__init__.py:165`) is used only for CSV import in `flymanager/app/routes/data.py:121`. There is no image-upload or blob-serving capability in the app.

## Decisions (confirmed with user)

- **Registry semantics:** an entry registers a token and *may* define a canonical mapping. Marker metadata (phenotype/effect, dominance, chromosome, body part) is captured only when the token is actually a visible marker.
- **Registry scope:** per-user, with export and import so one user's curation can be handed to another. Not lab-wide, not admin-approved.
- **Selection granularity:** both — per row you pick which flagged tokens to act on, and per token you pick which candidate to use.
- **Write path:** the reviewer writes directly to the record. Not a deep link back to the record editor.
- **Clean rows:** dropped entirely. Not hidden behind a toggle.
- **Bulk:** one row at a time. No cross-record "apply this token everywhere" action.
- **Architecture:** approach A — the materialized cache stays registry-agnostic and the viewer's registry is applied as an overlay at render time.
- **Marker metadata depth:** stored and displayed only. No image upload, and no wiring into phenotype prediction.

### Why approach A

A per-user registry collides with a per-record materialized cache: `StandardizationCache` is stamped with a FlyBase pipeline signature and embedded on the document, with no notion of who is looking at it. Two alternatives were considered and rejected:

- **Owner-scoped registry stamped into the cache** (each payload carries a registry revision; adding an entry stales the owner's records). Materialization stays authoritative, but it breaks the confirmed per-user model — you could not silence a token on a colleague's record you can see — and every registry edit triggers a backfill sweep.
- **Live recompute for users with a non-empty registry.** No schema change, but it reintroduces exactly the per-request cost the materialized cache exists to eliminate, and degrades as registries grow.

Approach A keeps the cache a record of raw, registry-free facts and makes the overlay pure set arithmetic at render time: no per-user cache, no cross-user invalidation, and registry edits take effect immediately for their author and nobody else.

## Architecture

### Part 1 — Registry storage

New collection **`marker_registry`**, one document per (user, token), with a unique index on `(User, TokenKey)`:

```
{
  User:           "<username>",
  Token:          "Nrg[849]",       # the token exactly as written in genotypes
  TokenKey:       "nrg[849]",       # case-normalized lookup key
  CanonicalToken: "",               # "" = accept as written; else the replacement
  IsMarker:       false,
  Marker: {                          # null unless IsMarker
    displayLabel, bodyPart, effect, dominance, chromosome
  },
  Notes:          "",
  CreatedAt, UpdatedAt
}
```

The two roles of an entry:

- `CanonicalToken == ""` — "this token is legitimate; stop flagging it." Genotype text is never rewritten.
- `CanonicalToken != ""` — the above, **plus** the token becomes a suggestion source: any other record of yours using it shows the mapping as a recommended replacement.

Registry entries are indexed into a small in-memory structure per request (`{TokenKey: entry}`), loaded once per reviewer page render.

**Export** is a JSON download of the requesting user's entries. **Import** accepts that JSON and merges by `TokenKey` under an explicit, user-chosen conflict policy — skip existing entries, or overwrite them. Importing a colleague's registry must never silently replace your own definitions, so there is no implicit default; the choice is required in the request payload.

### Part 2 — Cache v2 (registry-agnostic, complete)

Bump `STANDARDIZATION_CACHE_VERSION` to `2`. `summarize_genotype_standardization` stops emitting the lossy `topTokens` / `recommendedReplacements` slices and instead emits the **complete** issue list:

```
{
  issueCount, unresolvedCount, unmodeledCount,
  issues: [ { token, issueType, occurrenceCount,
              recommendedReplacement, recommendedSource }, ... ]
}
```

Still built with `candidate_limit=0`, so no fuzzy scan — the payload grows by a handful of short strings per record. The display slices the reviewer previously read from the cache are derived at render instead.

`_standardization_cache_is_current` already fails a `version` mismatch under `strict=True`, so every existing cache entry is stale on deploy and the existing `backfill_stock_standardization_cache` / `backfill_cross_standardization_cache` runs rebuild them. No bespoke migration.

Consumers of the old summary shape must be updated in the same change: `_build_standardization_reviewer_row` (`main.py:228`) and the reviewer template are the only readers of `topTokens` / `recommendedReplacements`.

### Part 3 — Viewer overlay and flagged-only queue

`_build_standardization_reviewer_rows` gains the viewer's registry index as a parameter. For each subject genotype:

1. Read the cached issue list (via `_stock_standardization_summary` / `_cross_standardization_summaries`, unchanged in their cache-preferring behaviour).
2. Drop every issue whose token matches a registry entry by `TokenKey`.
3. For surviving issues with no `recommendedReplacement`, fill one in from a registry entry that defines a `CanonicalToken` for that token.
4. Recompute `issueCount`, `unresolvedCount`, `unmodeledCount` from what survives.
5. **If nothing survives, do not emit a row.**

Because every emitted row is flagged, the existing flagged-first sort collapses to a stable ordering by record type and name.

Summary cards change to report honest numbers under the new model:

| Card | Value |
|---|---|
| Review targets | every genotype-bearing stock/cross parent accessible to you |
| Flagged targets | rows in the queue, split into stocks and cross parents |
| Open review issues | surviving issues, split unresolved vs unmodeled |
| Silenced by your registry | targets that would be flagged but are fully covered by your registry |

The last card matters: without it, adding a registry entry makes rows disappear with no visible accounting. With it, curation visibly moves a counter.

### Part 4 — Endpoints

All three live on the `main` blueprint alongside the reviewer, and all resolve the target through `get_accessible_stock` / `get_accessible_cross` so access control is the same as everywhere else.

**`GET /reviewer/detail`** — params `recordType` (`stock` | `cross_parent`), `uniqueID`, `subject` (`stock` | `male` | `female`), optional `token` + `query` to override a fuzzy search term. Runs `review_stock_standardization` on that one genotype **with** candidates (default `candidate_limit`), applies the registry overlay, and returns the surviving issues plus `canEdit` (from `ViewerCanEdit`). This is fired lazily when a row is expanded, never for the page as a whole — candidates are precisely the expensive scan the materialized cache was built to avoid.

**`POST /reviewer/apply`** — body `{ recordType, uniqueID, subject, expectedGenotype, replacements: [{ from, to }] }`:

1. Resolve the record; reject with 403 unless `ViewerCanEdit`.
2. **Optimistic lock:** if the record's current genotype for that subject differs from `expectedGenotype`, reject with 409 and return the current value. The client re-renders rather than clobbering a concurrent edit.
3. Rewrite the tokens (Part 5). A replacement whose `from` token is not present is an error — the whole request fails and nothing is written.
4. Persist through `edit_stock` / `edit_cross` with `refresh_vials=False`, updating `Genotype` or `MaleGenotype` / `FemaleGenotype`. `ModificationLog` and `DataModifiedDate` come for free via `apply_updates_to_owned_document`.
5. Rebuild `StandardizationCache` for the record and `$set` it.
6. `write_activity` describing the applied replacements.
7. Return the new genotype and the recomputed row state, so the row updates in place or leaves the queue.

**`POST /reviewer/registry`** — creates or updates an entry for the requesting user from a flagged token: `{ token, canonicalToken, isMarker, marker: {...}, notes }`. Returns the recomputed row state for the row the token came from. Companion routes for the management page: list, update, delete, `GET /reviewer/registry/export`, `POST /reviewer/registry/import`.

Apply and registry-write routes are rate-limited in line with the app's other mutating endpoints.

### Part 5 — The genotype token rewriter

The one piece where a bug corrupts data silently, and therefore a pure, separately-tested function:

```python
replace_genotype_tokens(genotype: str, replacements: dict[str, str]) -> str
```

It must not use string replacement. `str.replace("w-", "w[*]")` on `w-; CyO/PBac{y[+mDint2] w[+mC]=13XlexAop-IVS-mCyRFP3::Kir2.1}VK00002; TM2/TM6B;` corrupts the construct.

Implementation: a single bracket-depth-aware pass over the genotype that yields `(start, end)` spans for every raw token — the same boundaries `_iter_genotype_packages` and `tokenize_gene_package` produce together, i.e. splitting at depth 0 on `;`, `/`, `,`, and space, with `.strip()` / `.rstrip(":")` applied when comparing span text to a token. Spans whose text equals a `from` token are spliced with the replacement; everything else, including whitespace and separator style, is preserved byte-for-byte.

The alignment between this scanner and the parser is the correctness guarantee the whole feature rests on, so it gets a dedicated property-style test: for a corpus of genotypes drawn from the parser's own test cases, the tokens extracted by the scanner's spans must equal the concatenation of `tokenize_gene_package` output over `_iter_genotype_packages`. If those two ever drift, the reviewer would offer to replace a token it cannot correctly locate.

### Part 6 — UI

**Row.** Each flagged row keeps its summary and gains a **Review & Apply** disclosure button beside the existing `Open Stock` / `Open Cross` link, which stays — some fixes still need the full record editor. The "Recommended Replacements" column moves into the expanded panel; a list of `w- -> w[*]` pills you cannot act on is what makes the current page feel inert.

**Expanded panel.** Fetches `/reviewer/detail` once, then renders one block per surviving flagged token:

- *With candidates* — token, issue label, occurrence count; a select defaulting to the recommended replacement with the ranked fuzzy candidates beneath it (source, score, match basis, gene/insertion symbols, description — matching what `view_stock.html:944` already shows); a "Search again" input that re-queries via the `token`/`query` override; and a checkbox controlling inclusion in the apply.
- *Without candidates* — instead of today's dead-end copy, an **Add to my registry** action opening an inline form: accept-as-written or map to a canonical token, plus an "this is a visible marker" toggle revealing phenotype/effect, dominance, chromosome, and body part. Saving posts to `/reviewer/registry`; the token leaves the row immediately, taking the row with it if it was the last issue.

**Footer.** A before → after genotype preview computed client-side from the same replacement set the server will apply, then **Apply**. Not a modal confirmation — a visible consequence, so a wrong pick is obvious before the write rather than after. When `canEdit` is false the Apply control is absent rather than disabled, and the panel reads as review-only.

**Registry management page.** Its own page (`/reviewer/registry`, linked from the reviewer header next to Open Stocks / Open Crosses), since per-user data cannot live on the admin-gated settings page: list, edit, and delete entries; export to JSON; import with the required skip/overwrite choice.

## Testing

Test-first on the rewriter, then outward:

- **Rewriter** — whole-token replacement across chromosome arms; `w-` must not match inside `w[+mC]` within a construct; a token appearing in several packages; preservation of separators, spacing, and trailing semicolons; a `from` token that is absent raises rather than silently no-ops; the scanner/parser alignment property described in Part 5.
- **Overlay** — a registry entry silences its token; a row whose tokens are all silenced is absent from the queue; a registry `CanonicalToken` surfaces as a recommendation where none existed; recomputed counts and the "silenced" card agree.
- **Cache v2** — round-trip of the complete issue list; a v1 payload is stale under `strict=True` and is rebuilt by the existing backfill; the reviewer's non-strict read path still never recomputes on view.
- **Registry model** — uniqueness on `(User, TokenKey)`; case-normalized lookup; export/import round-trip; import honours skip and overwrite policies and rejects a request with neither.
- **Routes** — detail and apply for both stocks and cross parents; 403 for a viewer who can maintain but not edit; the 409 stale-genotype path; apply refreshes `StandardizationCache` so the returned row reflects the write; an unknown `from` token fails the whole request atomically.

Tests use the existing `tests/mongo_fakes.py` doubles, consistent with the rest of the suite.

## Out of scope

Deliberately excluded, and each is a real gap rather than an oversight:

- **Registry markers do not feed phenotype prediction.** The resolver and `PhenotypeCache` are a much larger blast radius and belong to the separate user-defined-markers brainstorm.
- **No image upload** for marker metadata — the app has no file store, and inventing one here would prejudge that future feature.
- **No cross-record bulk apply.** A token appearing in twelve records is twelve applies. Deferred until the per-row path is proven.
- **No undo** beyond `ModificationLog`, the activity feed, and editing the record again.
