# Unified Marker Image Resolution Design

Date: 2026-08-30
Status: approved design, ready for implementation planning

## Goal

Make every surface that shows a marker's reference photos resolve them the
same way, through one function, over one binding shape, rendered by one
template macro.

This is a correctness fix, not a feature. The marker catalog page shows no
photos at all for markers that visibly have photos elsewhere in the app,
because it is the one consumer that never adopted the shared mechanism.

A companion spec,
`docs/superpowers/specs/2026-08-30-marker-knowledge-into-catalog-design.md`,
covers the remaining divergence found in the same audit: marker knowledge
still hardcoded in Python that the catalog is designed to hold. It is
separate because it changes the catalog schema and therefore the prediction
signature; this spec changes neither.

## Background (verified against current code at df4b8c2)

Slice B (`docs/superpowers/specs/2026-08-29-marker-images-design.md`, merged)
moved the 254 shipped phenotype images into Mongo + GridFS and added a user
upload path. It left two binding shapes in `marker_images`:

| written by | `match.markerKeys` | `match.aliases` / `stem` / `bodyPart` |
|---|---|---|
| user upload (`upsert_image_entry`) | `[<catalog Key>]` | empty |
| shipped seed (`load_image_seed`) | `[]` (all 254) | populated |

`_score_entry` in `image_library.py` reads both: an entry whose `markerKeys`
contains one of the marker's reconstructed definition keys scores
`EXACT_KEY_SCORE` (1000), and everything else falls through to alias, stem
and body-part scoring. So the scorer is a superset reader, and the stock
viewer, cross viewer and phenotype preview — all of which call
`select_prediction_reference_images` — see both shapes correctly.

The docstring on `_marker_definition_keys` records that this exact-key tier
was added precisely because "uploads are invisible everywhere except the
marker detail page". Someone taught the viewers to see uploads. Nobody
taught the marker page to see the library.

### The four findings

**F1. One divergent reader.** `flymanager/app/routes/markers.py:220` resolves
images with `get_image_catalog()["by_marker_key"].get(key, [])`, an exact
index built only from `match.markerKeys`. Since every seeded image has an
empty `markerKeys`, no shipped photo can ever appear on a marker's own page.
Verified against production-mirrored data: `by_marker_key["CyO"]` returns 0
entries, while scoring the resolved `Cy` marker against the 254 entries
returns matches at scores 120 and 115.

**F2. Two binding shapes.** The table above. Nothing except `_score_entry`
knows both shapes exist, so any new consumer has to rediscover this.

**F3. Two image renderers.** `view_stock.html`, `view_cross.html` and
`stock/phenotype_preview.html` render through `render_phenotype_images` in
`_phenotype_images_macro.html`, which provides click-to-zoom (the overlay
base.html renders once as a direct child of `body`) and a placeholder card
for a marker with no image. `markers/detail.html` hand-rolls a `<figure>`
grid and has neither. Fixing F1 alone would show the right photos with the
wrong behaviour.

**F4. Marker knowledge in code rather than the catalog.** Covered by the
companion spec, not here.

### Balancers have no images of their own

Scoring `CyO` and `TM3` against all 254 entries returns 0 for every entry:
`_marker_aliases` on balancer metadata yields an empty set, because balancer
documents carry `symbol`/`family`/`default_markers` and none of the fields
`_marker_aliases` reads. The viewers show a balancer through the markers it
carries — `CyO` surfaces as `Cy`, `pr`, `cn`. That is the intended behaviour
here too, not a fallback.

## Non-goals

- Changing `_score_entry`'s scoring rules or their tuning. The scoring-parity
  test (`tests/test_marker_image_scoring_parity.py`) pins current behaviour
  and must stay green throughout.
- Changing the catalog schema, the prediction signature, or anything that
  triggers a phenotype cache rebuild.
- The marker catalog's form usability work (typo-proofing, tag inputs,
  upload-on-create). That sits on top of Part 3 and is specified separately.

## Part 1 — One image resolver

Add to `flymanager/utils/phenotypes/image_library.py`:

```python
def select_marker_images(definition_key, *, min_score=ALIAS_TIER_SCORE):
    """Every reference image for a catalog definition, best match first."""
```

`ALIAS_TIER_SCORE` is a new named constant (88) sitting beside the existing
`EXACT_KEY_SCORE`; the alias and stem tiers currently use bare literals
(98, 100, 88, 72) inside `_score_entry`, and naming the floor is the only
change made to that function.

It resolves the Key to the canonical resolved-marker dict — the same shape
the predictor produces and the viewers already pass in — then scores every
catalog entry with the existing `_score_entry` and returns all matches at or
above `min_score`, sorted by score descending then `imageId` for stability.

Key-to-marker resolution, by kind:

| kind | resolution |
|---|---|
| `gene_marker` | `get_visual_marker(key)` |
| `allele_marker` | `get_visual_marker(gene_stem, allele_spec, token=key)` |
| `construct_marker` | catalog `construct_markers[stem]`, overrides applied |
| `alias` | follow `payload.value` to its target and resolve that |
| `balancer` | one group per carried marker in `default_markers` |

A balancer returns groups rather than a flat list, so the page can say which
carried marker each photo belongs to. To keep one return shape, the function
returns a list of `{"marker_key", "display_label", "images": [...]}` groups;
a non-balancer returns exactly one group.

Each image row carries the fields the shared macro already consumes
(`image_id`, `image_url`, `has_image`, `display_label`, `body_part`,
`credit`, `provenance`, `notes`, `source_collection`, `match_score`) plus one
new field, `attached`, defined in Part 3.

`select_phenotype_reference_images` is refactored to share the resolution and
scoring helpers but keeps its exact current signature, return shape and
best-match-only behaviour. Its callers do not change.

### The score floor

The viewers keep only the single best image per marker, so the fuzzy tiers
rarely surface anything wrong. A detail page listing *every* match above zero
would surface the substring tier (score 72: the marker's alias appears
anywhere inside an image's filename stem), which is noisy.

The page therefore shows matches at the exact-key and alias tiers (>= 88) as
"Reference photos", and puts anything scoring 1..87 behind a collapsed
"Possibly related" disclosure. Nothing is silently dropped; the distinction
is presentation, and it is the one place where "same mechanism" deliberately
does not mean "same output".

### Deleting the divergent index

`by_marker_key` is removed from `compile_image_catalog`'s snapshot once
`markers.py` stops reading it, so a future consumer cannot reintroduce the
bug by reaching for the obvious-looking index. Callers to update:

- `flymanager/app/routes/markers.py:220` — the fix itself.
- `flymanager/utils/phenotypes/image_catalog.py:24-28` — index construction.
- `tests/test_marker_image_catalog.py:14` — the only assertion pinning it;
  rewritten to assert the same ordering through `select_marker_images`.
- Four test files construct empty snapshots containing the key literally;
  those literals are updated.

## Part 2 — One binding shape

Backfill `match.markerKeys` on the shipped seed entries so both writers
produce the same shape and `_score_entry`'s exact tier does the work for
every image, seeded or uploaded.

The backfill is derived, not hand-written: for each seed entry, resolve its
`aliases` and `stem` against the compiled catalog and record the definition
Keys that currently match at the alias tier or above. This is the same
relation the scorer computes at read time, materialised at seed time.

Constraints this must respect:

- **The seed is content-addressed.** `load_image_seed` verifies each file's
  byte count and SHA-256 against `data/markers/images/index.json` and raises
  on mismatch. Changing `index.json` entries means regenerating it through
  the existing tooling, not editing it by hand; the image bytes themselves do
  not change, so the hashes do not change.
- **Idempotence.** `load_image_seed` skips any record whose `sha256` is
  already present, so a backfill that only adds `markerKeys` to `index.json`
  will not reach an already-seeded database. The migration therefore needs an
  explicit one-shot update over `marker_images` for existing installs, run
  from the same job path as other marker maintenance, followed by
  `bump_image_revision`.
- **Image metadata must stay out of the prediction signature.** This is a
  standing catalog invariant: changing an image binding must not invalidate
  materialised phenotype caches. `SIGNATURE_EXCLUDED_FIELDS` and the fact
  that `marker_images` is a separate collection from `marker_definitions`
  already guarantee this; the migration adds no definition-side write, and a
  test asserts the catalog signature is unchanged across it.
- **Scoring parity.** `tests/test_marker_image_scoring_parity.py` pins the
  best-match choice for a set of real markers. Populating `markerKeys`
  promotes some entries from the alias tier (98) to the exact tier (1000),
  which can change which entry wins where two previously tied. The migration
  is only correct if that test still passes; if a case legitimately changes,
  the expectation is updated in the same commit with the reason recorded.

## Part 3 — One image renderer

`markers/detail.html` drops its bespoke `<figure>` grid and calls
`render_phenotype_images`, gaining click-to-zoom and placeholder cards. The
page loads `css/phenotype_images.css` and `js/marker_image_zoom.js`, as the
macro's docstring requires.

The macro grows one distinction it does not have today, because the marker
page is the first surface where both kinds appear side by side:

- **Attached** — the image's `match.markerKeys` contains this marker's Key.
  Someone deliberately bound it here. It is deletable (subject to the
  existing `origin == 'user'` / admin gate) and sorts first.
- **Matched** — the image scored above the floor by description. There is no
  binding to this marker to remove, so no Delete control is rendered, and
  after Part 2 this category contains only images whose alias match was too
  weak to be materialised.

The row field `attached` carries this; the macro renders Delete only when
`attached` and the caller passed a delete endpoint. For the three existing
callers, `attached` is absent and nothing changes.

Balancer pages render one macro call per carried-marker group under a
subheading naming the marker.

## Testing

Written before the fix, red for the right reason first.

**Part 1**
- A seeded, alias-bound image appears on its marker's detail page. This is
  the regression test for F1 and must fail against current `main`.
- A balancer's page shows its carried markers' images, grouped and labelled.
- An alias definition resolves to its target's images.
- Matches below the floor land in "possibly related", not in the main grid.
- `select_phenotype_reference_images` returns byte-identical results to
  today for the parity fixture set after the refactor.

**Part 2**
- Every seed entry's derived `markerKeys` resolve to real catalog Keys.
- The catalog signature is identical before and after the migration.
- The migration is idempotent: running it twice changes nothing the second
  time and bumps the image revision only when it actually wrote.
- Scoring parity holds.

**Part 3**
- The marker detail page emits the shared macro's markup, including the zoom
  trigger and a placeholder card when a marker has no images.
- Delete renders for an attached user upload and does not render for a
  matched library image.

Full-suite check against the documented pre-existing baseline
(`memory/running-tests-locally.md`) before declaring done.

## Risks

- **Parity drift in Part 2** is the main one; it is why parity is a gate
  rather than an afterthought.
- **The migration touches production data.** It is additive (`$addToSet` on
  `match.markerKeys`), reversible by unsetting the field, and does not touch
  image bytes or definitions.
- **Removing `by_marker_key`** is a breaking change to the snapshot shape.
  It is process-internal, not persisted, so the blast radius is the four test
  files and the one route listed in Part 1.
