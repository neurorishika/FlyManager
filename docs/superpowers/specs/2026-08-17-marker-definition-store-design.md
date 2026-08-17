# Unified Marker Definition Store Design

Date: 2026-08-17
Status: approved design, ready for implementation planning

## Goal

Replace FlyManager's hardcoded visual-marker system with a single dynamic
definition store. Markers ship with the system, users add their own, and an
admin can promote a user definition to curated status. One entity, one editing
surface, one compilation step, one cache signature.

This is the first of three slices. Image attachment and binary expression
systems (Gal4/UAS, LexA/LexAop, QF/QUAS) are designed separately; this slice
leaves explicit, empty extension points for both.

## Background (verified against current code)

The marker model is hardcoded across six sites, not one:

| File | Hardcoded structure |
|---|---|
| `flymanager/utils/phenotypes/visual_markers.py` | `CRITICAL_MARKERS` (line 3), `VISUAL_MARKER_DICTIONARY` (53), `ALLELE_VISUAL_MARKER_DICTIONARY` (626), `REVIEWED_MARKER_ALIASES` (763), `BALANCER_METADATA` (785), `BALANCER_ALIASES` (870) |
| `flymanager/utils/phenotypes/construct_markers.py:5-63` | `CONSTRUCT_MARKER_RE` plus an if/elif chain that *is* the definition of mini-white / y+ / v+ |
| `flymanager/utils/phenotypes/epistasis.py` | two rules in Python (`w` x mini-white rescue, `cn`+`bw`) |
| `flymanager/utils/phenotypes/image_library.py:11-47` | `PHENOTYPE_IMAGE_ALIASES`, `BODY_PART_ALIASES` |
| `flymanager/utils/constraints/marker_stability.py:3-22` | `MARKER_STABILITY_SCORES`, `_MARKER_NOTES` |
| `flymanager/utils/phenotypes/flybase_pipeline.py:34-35` | `DRIVER_KEYWORDS`, `REPORTER_KEYWORDS` |

Three facts constrain the design:

1. **The resolution path has no database handle.** `compute.py:64` calls
   `resolve_package_markers(package)` with no `db`; the whole chain reads a
   process-memoized JSON snapshot (`flybase_pipeline._IN_MEMORY_CACHE`). Any
   dynamic store must not put a Mongo query on that path.
2. **`pipelineSignature` covers only FlyBase files.**
   `compute_flybase_pipeline_signature()` (`flybase_pipeline.py:271`) hashes
   FlyBase source file sizes and mtimes. `PhenotypeCache` and
   `StandardizationCache` stamp it. If a marker edit does not move a signature,
   every stored prediction is silently wrong with no staleness signal.
3. **A DB-overlay precedent already exists.** `balancer_selection.py:28` merges
   hardcoded `BALANCER_METADATA` with a `balancer_definitions` Mongo collection
   ingested from BDSC (`balancer_ingest.py:393`). This slice folds that
   half-overlay into the general mechanism rather than leaving two.

Binary expression systems are effectively unmodeled today.
`DRIVER_KEYWORDS = ("GAL4", "LexA", "QF", "FLP", "FLPo", "Cre")` only *labels* a
construct `category: "driver"` from FlyBase description text. Nothing pairs a
driver with a responder. `lookup_split_system_annotations` (`compute.py:135`)
keys off the **entire normalized genotype string** matched exactly against a
FlyBase table, so it fires almost never. That gap is slice C.

## Decisions (confirmed with user)

- **Visibility:** a user-added marker is live for everyone as soon as it is
  saved. Admin promotion changes trust labelling and edit permissions only.
  This keeps marker resolution a single global computation, which
  `PhenotypeCache` depends on.
- **Resolution:** definitions live in Mongo and are compiled into an in-process
  indexed snapshot with a content hash. Resolution stays db-less.
- **Coverage:** all declarative metadata migrates, including `CRITICAL_MARKERS`.
  Epistasis stays Python but references catalog keys instead of literals.
- **Layering:** shipped defaults stay a version-controlled repo file, never
  written at runtime. Mongo holds only deltas: user additions and explicit
  overrides of a shipped key.
- **Invalidation:** targeted rebuild by affected token, not full recompute.

### Why compiled snapshot rather than threading `db`

Threading `db` from `compute.py` down through `resolver.py` and
`visual_markers.py` would add a database dependency to functions that are
currently pure and trivially testable, and would put per-lookup Mongo traffic on
a hot path. The compile step keeps the existing db-less contract intact; the
only new cost is a cheap revision probe described in Part 3.

### Why no on-disk compiled artifact

`flybase_pipeline` writes `PHENOTYPE_EVIDENCE_CACHE.json` because parsing the
FlyBase TSV bundle takes minutes. The marker catalog is a few hundred rows and
compiles in milliseconds. A second on-disk cache would add a staleness surface
for no gain, so compilation is in-process only.

## Architecture

### Part 1 — The entity

New Mongo collection `marker_definitions`, unique index on `Key`. One envelope,
`kind` discriminator, kind-specific payload:

```
{
  Key: "wg[Gla-1]",              # stable identity
  kind: "allele_marker",         # gene_marker | allele_marker | alias
                                 # | balancer | construct_marker
  match:   { ... },              # kind-specific matching rules
  payload: { ... },              # kind-specific semantics

  sorting: { stabilityScore, notes: [] },      # absorbs marker_stability.py
  audit:   { isProbeMarker: bool, probeSymbol: "" },   # absorbs CRITICAL_MARKERS
  imaging: { aliases: [], images: [] },        # extension point -> slice B
  expression: { },                             # extension point -> slice C

  provenance: { geneName, flybaseId, referenceUrl, source },
  origin: "user" | "curated",                  # shipped rows are NOT in Mongo
  overridesShipped: { key, shippedVersion },   # set when overriding a built-in
  CreatedBy, CreatedAt, UpdatedBy, UpdatedAt, CuratedBy, CuratedAt
}
```

The five kinds:

| kind | replaces | match | payload |
|---|---|---|---|
| `gene_marker` | `VISUAL_MARKER_DICTIONARY` | bare token | bodyPart, effect, dominance, displayLabel, phenotypeKey, chromosome, homozygousLethal, scoringConfidence |
| `allele_marker` | `ALLELE_VISUAL_MARKER_DICTIONARY` | `gene[allele]` | as above plus geneStem |
| `alias` | `REVIEWED_MARKER_ALIASES` | bare token | aliasType, canonical token |
| `balancer` | `BALANCER_METADATA` + `balancer_definitions` | symbol plus aliases | chromosome, family, defaultMarkerKeys[], notes[] |
| `construct_marker` | the if/elif at `construct_markers.py:35-63` | geneStem plus allele prefix (`w` + `+`) | marker field overrides and flags (`miniWhite`) |

Two capabilities this adds that the current code cannot express: a user-added
marker can carry a sorting-stability score and image aliases (today those live
in two unrelated hardcoded tables keyed by display label), and
`balancer_definitions` stops being a parallel half-overlay.

**`CRITICAL_MARKERS` migrates as an envelope flag, not a list or a derived
threshold.** It is not derivable from marker metadata: the confidence values of
its 46 entries span 0.55-0.98, entirely enclosing the 0.66-0.74 range of the
three non-critical entries, so no threshold separates them. And `Sp` appears in
`CRITICAL_MARKERS` with no `VISUAL_MARKER_DICTIONARY` row at all -- it resolves
through the alias `wg[Sp-1]` -- so a query restricted to `gene_marker` rows
would silently drop it. The flag therefore sits on the envelope and is settable
on any `kind`. `examine_flybase_directory` (`examiner.py:117-119`) takes its
default from `[d.audit.probeSymbol or d.Key for d in catalog if
d.audit.isProbeMarker]`.

### Part 2 — Three layers

1. **Shipped:** `data/markers/catalog.json`, version-controlled, never written
   at runtime, carrying a `catalogVersion`. This matches the existing
   convention -- `data/phenotype_images/manifest.json` is already a tracked
   shipped data file. Generated once by a migration script from the current
   Python literals.
2. **Overlay:** `marker_definitions` in Mongo, deltas only.
3. **Compiled snapshot:** shipped layer, then overlay applied by `Key`,
   producing the indexed lookup structures the resolver needs.

An override records `overridesShipped.shippedVersion`, so a later release that
changes the built-in can be detected and surfaced as "the shipped definition
changed under your override" rather than silently diverging.

### Part 3 — Compilation, freshness, and signature

New module `flymanager/utils/phenotypes/marker_catalog.py`:

- `load_shipped_catalog(path=None)` — reads and validates the shipped file.
- `compile_catalog(shipped, overlay_documents)` — returns an indexed snapshot
  plus `signature`.
- `get_catalog()` — returns the current in-process snapshot. Never touches
  Mongo.
- `refresh_catalog(db, *, force=False)` — recompiles from Mongo if the stored
  revision moved (or `force`).
- `compute_marker_catalog_signature(snapshot)` — hash over `catalogVersion`
  plus canonicalized overlay documents sorted by key. Deterministic.

**Freshness.** The settings singleton gains `markerCatalogRevision`, bumped on
every definition write. A module global holds the compiled snapshot and the
revision it was built from. A `before_request` hook re-checks the revision at
most once every 30 seconds per process and recompiles if it moved. The revision
being authoritative in Mongo is what makes this multi-process-safe: every worker
converges within the interval rather than each holding a divergent lazy cache.
Background rebuild jobs call `refresh_catalog(db, force=True)` rather than
waiting on the interval.

A 30-second staleness window is acceptable because predictions are materialized
anyway; the targeted rebuild job, not the request path, is what updates records.

**Signature placement.** Both cache envelopes gain `markerCatalogSignature`
alongside the existing `pipelineSignature`, and both strict-mode checks
(`get_cached_stock_phenotype` / `get_cached_cross_phenotype` at
`predictor.py:425` and `:526`, and the standardization equivalents) compare it.

`StandardizationCache` needs this as much as `PhenotypeCache`:
`stock_standardization.py:397-432` reads `VISUAL_MARKER_DICTIONARY`,
`ALLELE_VISUAL_MARKER_DICTIONARY`, and `REVIEWED_MARKER_ALIASES` to decide what
counts as a flagged token, so adding a marker changes what the reviewer flags.
The reviewer-apply spec already bumps `STANDARDIZATION_CACHE_VERSION` to 2;
whichever of the two lands second inherits the other's envelope shape.

### Part 4 — Constants become accessors

Six exported constants are consumed as live dicts or sets, and two are evaluated
at **import time** -- `parser.py:13-14` computes `KNOWN_BALANCERS` and
`BALANCER_MATCH_ORDER` when the module loads, which permanently freezes balancer
recognition to the shipped set. Every one becomes a function call against the
current snapshot:

| Consumer | Constants used |
|---|---|
| `parser.py:5-14,67-73,118` | `KNOWN_BALANCER_SYMBOLS`, `BALANCER_ALIASES`, `BALANCER_MARKERS` (import-time) |
| `resolver.py:146` | `BALANCER_MARKERS` |
| `balancer_selection.py:23` | `BALANCER_METADATA` |
| `stock_standardization.py:397-432` | all three marker dicts |
| `examiner.py:595-637` | all three marker dicts plus `CRITICAL_MARKERS` |
| `tests/test_phenotype_experiments.py:32` | `BALANCER_MARKERS` |

The three `get_*` functions (`get_visual_marker`, `get_balancer_metadata`,
`get_reviewed_marker_alias`) keep their exact signatures and are reimplemented
over the snapshot, so `flybase_ingest.py:13` and `construct_markers.py:3` need
no changes.

`balancer_selection.py` additionally stops reading the `balancer_definitions`
collection directly; those documents are ingested into `marker_definitions` as
`balancer`-kind rows by an extension to `ingest_balancer_definitions`
(`balancer_ingest.py:393`).

### Part 5 — Targeted rebuild

On every definition write, derive an **affected token set**, then rebuild only
records whose genotype contains one of those tokens.

The set is not just the edited key. Editing `Cy` must also rebuild every record
containing `CyO`, `SM1`, `SM5`, `SM6a`, `SM6b`, because those balancers list
`Cy` in `defaultMarkerKeys` and their markers are resolved by reference at
`resolver.py:146`. **The affected set is therefore closed over reverse
references:** any balancer referencing the edited key contributes its symbol and
its aliases.

Matching is a substring regex on `Genotype` (`re.escape`d, `$or` across the set;
`MaleGenotype`/`FemaleGenotype` for crosses). Substring matching over-matches --
editing `B` sweeps most of the collection -- and that is the deliberate
direction of error: extra rebuilds are wasted work, missed rebuilds are silently
wrong predictions.

Two edit kinds cannot be scoped and fall back to a full rebuild:

- `construct_marker` changes, because the match is a pattern inside construct
  bodies rather than a whole token.
- Deleting a shipped override, because the restored shipped definition's blast
  radius is not derivable from the deleted row alone.

**Stamping the remainder.** After the targeted rebuild, one `update_many` sets
the new `markerCatalogSignature` on records that were *not* affected, filtered
to those whose cache is otherwise current (matching `version`,
`pipelineSignature`, and genotype). Without this, the next `force=False`
backfill would rebuild the entire collection anyway and the targeting would buy
nothing.

The risk is explicit: stamping means a bug in affected-token derivation is
permanently hidden rather than self-correcting on the next backfill. The
mitigations are the existing guarded force-recompute (typed phrase, 24-hour
cooldown, `cache_force_refresh.py`) as an escape hatch, and a dedicated test for
the reverse-reference closure.

Rebuilds run as a background job via the existing `enqueue_job` pattern
(`flymanager/app/jobs/`), reusing the `backfill_*` wrappers with `force=False`.

### Part 6 — Ownership and promotion

- Any logged-in user creates definitions; they go live for everyone on the next
  catalog refresh.
- Edit and delete: the creator or admin. Once **curated**, admin only.
- Promotion is an admin action setting `origin: "curated"` plus `CuratedBy` and
  `CuratedAt`. It changes trust labelling and edit permissions only -- there is
  no behavioural change, because user markers are already live. Promotion is how
  a definition becomes stable enough that its author can no longer silently
  change it under everyone.
- Deleting an override restores the shipped definition. Deleting a user-created
  definition removes it.
- Every write bumps `markerCatalogRevision`, calls `write_activity` with
  attribution, and enqueues the rebuild.

Given global-immediate visibility, attribution is the real safety mechanism:
every marker in the UI shows who defined it, and the activity feed records each
change. Note that `admin_required` (`auth.py:70-78`) is
`session["username"] == "admin"` -- a single account, not a role flag.

### Part 7 — Routes and UI

New blueprint or route group:

- `GET /markers` — catalog: search, filter by kind, origin, body part,
  chromosome. Shipped rows render read-only with an **Override** action; user
  rows are editable by their creator.
- `GET /markers/<key>` — detail and edit, with the kind selector driving which
  payload fields appear. Admin sees a **Promote** action.
- `POST /markers` — create.
- `POST /markers/<key>` — update. 403 unless creator or admin; 403 for curated
  rows unless admin.
- `POST /markers/<key>/delete` — delete, same permission rule.
- `POST /markers/<key>/promote` — admin only.

Linked from the reviewer registry page, which is where the "this token is a real
marker" path lands.

### Part 8 — Extension points

- `imaging.aliases` **is used immediately**: it replaces
  `PHENOTYPE_IMAGE_ALIASES`, so `image_library.py` reads aliases from the
  catalog instead of a hardcoded table. `BODY_PART_ALIASES` stays hardcoded --
  it is a vocabulary of anatomical synonyms, not marker data.
- `imaging.images[]` stays empty until slice B adds upload.
- `expression{}` stays entirely empty and unshaped for slice C. Guessing at a
  driver/responder model before designing it would constrain that slice for no
  benefit.

## Data model changes

- New collection `marker_definitions`, unique index on `Key`.
- New tracked file `data/markers/catalog.json`.
- Settings singleton gains `markerCatalogRevision` (integer, absent means 0).
- `PhenotypeCache` and `StandardizationCache` envelopes gain
  `markerCatalogSignature`; both cache versions bump.
- `balancer_definitions` becomes an ingestion staging collection only; it is no
  longer read at resolution time.

## Error handling

- A malformed shipped catalog file is a startup failure, not a degradation --
  serving predictions from a half-loaded marker set would corrupt caches.
- A malformed overlay document is skipped with a logged warning and surfaced in
  the `/markers` UI as invalid, so one bad row cannot take the app down.
- Compilation failure leaves the previous snapshot in place and logs; the
  request path never blocks on a recompile.
- Per-record rebuild failures are contained the way `materialized_cache.py`
  already contains them, and counted in the job result.
- Duplicate `Key` on create returns 409.

## Testing strategy

TDD against `FakeDatabase` in `tests/mongo_fakes.py`, matching the existing
pattern.

- **Migration fidelity** — compiling the shipped catalog with an empty overlay
  reproduces today's `VISUAL_MARKER_DICTIONARY`,
  `ALLELE_VISUAL_MARKER_DICTIONARY`, `REVIEWED_MARKER_ALIASES`,
  `BALANCER_METADATA`, and `BALANCER_ALIASES` exactly, and the derived probe
  list equals all 46 `CRITICAL_MARKERS` symbols including `Sp`. This turns "did
  I transcribe 922 lines correctly" from a review problem into a test.
- **Compile and overlay** — override by key wins over shipped; deleting an
  override restores shipped; signature is stable for identical inputs and
  differs for any change.
- **Reverse-reference closure** — editing `Cy` yields an affected set containing
  `CyO`, `SM1`, `SM5`, `SM6a`, `SM6b`.
- **Targeted rebuild and stamping** — affected records recompute; unaffected
  current records are stamped without recompute; records stale for other reasons
  (genotype or `pipelineSignature` mismatch) are not stamped.
- **Unscopable edits** — a `construct_marker` edit and a shipped-override delete
  both trigger a full rebuild.
- **Freshness** — a revision bump causes recompile within the interval; a
  process holding a stale snapshot picks up another process's write.
- **Permissions** — non-creator cannot edit; curated rows are admin-only;
  promotion requires admin; duplicate key returns 409.
- **Accessor refactor** — balancer recognition in `parser.py` picks up a
  user-defined balancer added after import, which is impossible today.

## Out of scope

- Image upload and attachment (slice B).
- Binary expression system modelling (slice C).
- Epistasis as data. Rules stay Python and reference catalog keys.
- Per-user marker visibility, and any approval queue before a definition goes
  live.
- Wiring `DRIVER_KEYWORDS`/`REPORTER_KEYWORDS` into the catalog; those belong
  with slice C's expression model.

## Required amendment to the reviewer-apply spec

`docs/superpowers/specs/2026-08-17-reviewer-apply-and-registry-design.md`
defines a per-user `marker_registry` whose entries carry an `IsMarker` flag and
a `Marker{displayLabel, bodyPart, effect, dominance, chromosome}`
sub-document. That would create a second, private marker store beside this one
-- exactly the hardcoded-versus-dynamic split this slice removes.

That spec must drop `IsMarker` and `Marker{...}`. Its registry keeps only what
is genuinely per-user and genuinely about review: "accept as written" and "map
to canonical" token silencing. Its marker branch becomes a link into this
catalog. The amendment lands with whichever slice is implemented second.
