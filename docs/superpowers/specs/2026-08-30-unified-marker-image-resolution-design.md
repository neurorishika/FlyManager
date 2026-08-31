# Unified Marker Image Resolution Design

Date: 2026-08-30
Status: approved design, ready for implementation planning

## Goal

Make every surface that shows a marker's reference photos resolve them the
same way, through one function, ranked by one deterministic rule, rendered
by one template macro.

This is a correctness fix, not a feature. The marker catalog page shows no
photos at all for markers that visibly have photos elsewhere in the app,
because it is the one consumer that never adopted the shared mechanism.

A companion spec,
`docs/superpowers/specs/2026-08-30-marker-knowledge-into-catalog-design.md`,
covers the remaining divergence found in the same audit: marker knowledge
still hardcoded in Python that the catalog is designed to hold. It is
separate because it changes the catalog schema and therefore the prediction
signature; this spec changes neither.

## Background (verified against current code at de69c19)

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

- Changing `_score_entry`'s alias, stem and substring tiers or their tuning.
  The scoring-parity test (`tests/test_marker_image_scoring_parity.py`) pins
  current behaviour and must stay green throughout. Part 2 makes one narrow,
  argued exception for the exact-key tier, which is unranked today.
- Migrating the stored image documents. An earlier draft proposed this; see
  Part 2 for why it was dropped.
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

The tiers inside `_score_entry` are bare literals today: 100 exact stem, 98
alias, 88 stem *prefix*, 72 substring, plus bonuses of up to +18. They are
given names (`EXACT_STEM_SCORE`, `ALIAS_SCORE`, `STEM_PREFIX_SCORE`,
`SUBSTRING_SCORE`) so the floor can be stated in terms of a tier rather than
a magic number.

It resolves the Key to the canonical resolved-marker dict — the same shape
the predictor produces and the viewers already pass in — then scores every
catalog entry with the existing `_score_entry` and returns all matches at or
above `min_score`, sorted by score descending then `imageId` for stability.

Key-to-marker resolution, by kind:

| kind | resolution |
|---|---|
| `gene_marker` | `get_visual_marker(key)` |
| `allele_marker` | `get_visual_marker(gene_stem, allele_spec, token=key)` |
| `construct_marker` | the definition's `match.geneStem` indexes `construct_markers`; its `overrides` are merged over the base marker |
| `alias` | the compiled alias is a flat `{alias_type, value}` dict; resolve `value` as a Key |
| `balancer` | one group per carried marker in `default_markers` |

Resolution failures are explicit rather than silent: an alias whose `value`
names no definition, an alias chain (an alias pointing at another alias,
which is followed at most once and then abandoned), a construct with no
matching stem, and a balancer listing a `default_markers` entry that has no
definition all yield an empty group carrying the unresolved key, so the page
can say "no images, and this marker does not resolve" instead of rendering
nothing.

A balancer returns groups rather than a flat list, so the page can say which
carried marker each photo belongs to. To keep one return shape, the function
returns a list of `{"marker_key", "display_label", "images": [...]}` groups;
a non-balancer returns exactly one group.

Each image row carries the fields the shared macro already consumes
(`image_id`, `image_url`, `has_image`, `display_label`, `body_part`,
`effect`, `credit`, `provenance`, `notes`, `source_name`,
`source_collection`, `marker_key`, `match_score`) plus one new field,
`attached`, defined in Part 3. `marker_key` is required, not optional: the
placeholder card's "add an image" link is built from it, and a card without
it degrades to a bare catalog-search link.

`select_phenotype_reference_images` is refactored to share the resolution and
scoring helpers but keeps its exact current signature, return shape and
best-match-only behaviour. Its callers do not change.

### The score floor

The viewers keep only the single best image per marker, so the fuzzy tiers
rarely surface anything wrong. A detail page listing *every* match above zero
would surface the substring tier (72: the marker's alias appears anywhere
inside an image's filename stem) and the prefix tier (88: for the marker `B`,
any stem *starting* with the letter b), both of which are noisy in a list.

The floor is therefore `ALIAS_SCORE` (98), not 88: "Reference photos" shows
the exact-key, exact-stem and alias tiers, and everything scoring 1..97 goes
behind a collapsed "Possibly related" disclosure. Nothing is silently
dropped; the distinction is presentation, and it is the one place where "same
mechanism" deliberately does not mean "same output".

Note the floor filters post-bonus scores, so a substring match can in
principle reach 94 through bonuses and a prefix match can clear 98. On
current data no entry does — the manifest bonus never coexists with the stem
tiers, because all 32 manifest entries carry aliases — but the floor is a
presentation heuristic, not a guarantee, and should be described as one.

### Deleting the divergent index

`by_marker_key` is removed from `compile_image_catalog`'s snapshot once
`markers.py` stops reading it, so a future consumer cannot reintroduce the
bug by reaching for the obvious-looking index. Callers to update:

- `flymanager/app/routes/markers.py:220` — the fix itself.
- `flymanager/utils/phenotypes/image_catalog.py:24-28` — index construction,
  and the `_SNAPSHOT` default at line 10, which contains the key literally.
- `tests/test_marker_image_catalog.py:14` — the only assertion pinning it;
  rewritten to assert the same ordering through `select_marker_images`.
- Four test files construct empty snapshots containing the key literally;
  those literals are updated.

## Part 2 — A deterministic exact tier

An earlier draft of this spec proposed backfilling `match.markerKeys` onto
the 254 seeded images so both writers produced one binding shape. That is
dropped. Simulating it showed it changes which photo wins for nine markers
— `B`, `Bar`, `Cy`, `Sb`, `Ser`, `wa`, `Dr[Mio]`, `sna[Sco]`, `wg[Gla-1]` —
which are among the most-viewed in the app, and it would have done so for no
reader-visible benefit, since `_score_entry` already reads both shapes.

The simulation is worth keeping, because it exposed a latent defect that is
the actual mess here:

**`_score_entry` returns a flat `EXACT_KEY_SCORE` (1000) with none of the
bonuses the alias tiers receive, and `select_phenotype_reference_images`
picks a winner with a strict `score > best_score`.** Two images that both
match a marker by key therefore tie at 1000, and the winner is whichever the
snapshot happens to sort first. Today's ranked 120-vs-115 alias pairs are
decided on merit; two exact matches are decided by accident.

This is already reachable without any migration: upload two photos for the
same marker and which one every stock and cross view shows is unspecified.
The backfill did not create this — it detonated it 34 times at once.

The fix is to make the exact tier ranked rather than flat:

- The exact-key tier keeps its 1000 base, and then receives the same bonuses
  the alias tiers already get (body part +8, `learning_to_fly` +4, manifest
  +6, `display.priority`), so an exact match that also agrees on body part
  outranks one that does not.
- Winner selection gets an explicit, documented tie-break — `(-score,
  display.sortOrder, imageId)` — so equal scores resolve the same way on
  every process and every deploy, rather than by snapshot iteration order.

This is a deliberate, narrow exception to the "do not change `_score_entry`"
non-goal, and it changes no current behaviour: no marker has two exact-key
matches in the shipped data today, which is why the parity test does not move.
That must be asserted, not assumed.

With this in place the two binding shapes are no longer a hazard, and
unifying them becomes an optional cleanup with no user-visible effect — worth
revisiting only if a future feature needs it.

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
- **Matched** — the image scored above the floor by description. Nobody bound
  it here and there is nothing to unbind, so no Delete control is rendered.
  With the migration dropped, this is where all 254 shipped images land, and
  they stay undeletable from this page.

The row field `attached` carries this; the macro renders Delete only when
`attached` is true and the caller passed a delete endpoint. For the three
existing callers, `attached` is absent and nothing changes.

This gate is load-bearing rather than cosmetic. `delete_marker_image`
(`flymanager/app/routes/markers.py:293`) only unbinds when the image has more
than one marker key and otherwise deletes the document and its bytes
outright, and an admin may delete a `shipped` entry. Rendering Delete for a
merely-matched image would therefore put "remove this shipped photo from the
whole app" one click away on a page where the user believes they are editing
one marker.

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
- No marker in the shipped data has two exact-key matches. This is the
  precondition that makes re-ranking the exact tier a no-op today; when it
  stops holding, this assertion fails rather than the behaviour drifting.
- Two images both bound to one marker resolve in a documented order, and that
  order is stable across a recompiled snapshot with the entries reversed.
- An exact match that agrees on body part outranks one that does not.
- Scoring parity holds unchanged.

**Part 3**
- The marker detail page emits the shared macro's markup, including the zoom
  trigger and a placeholder card when a marker has no images.
- Delete renders for an attached user upload and does not render for a
  matched library image. Asserted at the rendered-HTML level, not just on the
  row data, because this is the gate that stands between an admin and
  destroying a shipped image for every view in the app.

Full-suite check against the documented pre-existing baseline
(`memory/running-tests-locally.md`) before declaring done.

## Risks

- **Changing the exact tier's scoring** (Part 2) is a deliberate exception to
  this spec's own non-goal. It is safe only because no marker currently has
  two exact-key matches; that fact is asserted by a test, so the day it stops
  being true the assertion fails rather than the behaviour drifting.
- **Removing `by_marker_key`** is a breaking change to the snapshot shape. It
  is process-internal, not persisted, and each process compiles its own
  snapshot, so a rolling deploy is safe; the blast radius is the one route
  and the five test literals listed in Part 1.
- **Part 3 surfaces images on a page that has a Delete control.**
  `delete_marker_image` only unbinds when `len(markerKeys) > 1` and otherwise
  destroys the image and its bytes. Rendering Delete for anything not
  key-bound to this marker would therefore let one click remove a shipped
  image from the stock and cross viewers. This is why "attached" gates the
  control, and it is the single most important thing to get right in Part 3.
