# Marker Knowledge Into The Catalog Design

Date: 2026-08-30
Status: approved design, ready for implementation planning

## Goal

Move the marker knowledge still hardcoded in Python into the marker catalog,
so that a lab which adds or corrects a marker through the UI gets the same
behaviour as a marker that shipped with FlyManager.

This is the second half of the phenotype-consistency audit. The first half,
`docs/superpowers/specs/2026-08-30-unified-marker-image-resolution-design.md`,
unifies how images are resolved and changes no schema. This spec changes the
catalog schema and therefore the prediction signature. It goes second because
both specs rewrite `image_library.py` and serialising them avoids a collision
— not, as an earlier draft claimed, because the signature forces an ordering.

**This spec was cut down after an adversarial review.** K3's epistasis half is
impossible as originally written and is dropped; K1's motivation was
factually wrong and its design is rebuilt around what the code actually does.
The review's findings are recorded inline rather than quietly edited out, so
the next reader can see why the scope is what it is.

## Background (verified against current code at de69c19)

Slice A moved the marker dictionaries out of Python and into
`data/markers/catalog.json` plus the `marker_definitions` overlay. The audit
confirms that resolution itself is now genuinely single-mechanism: every
consumer reads through the `visual_markers` accessors over the compiled
snapshot, and no route or template reimplements marker lookup.

Three pockets of marker knowledge did not make that move. Each one is a place
where the catalog's own mechanism exists and one rule bypasses it.

### K1. Balancer preference order (motivation corrected)

`flymanager/utils/constraints/_shared.py:10`:

```python
DEFAULT_BALANCER_PRIORITY = {
    1: ["FM7c", "FM7a", "FM7", "FM3"],
    2: ["CyO", "SM6a", "SM5", "SM1"],
    3: ["TM3", "TM6B", "TM6", "TM2", "TM1"],
    4: [],
}
```

An earlier draft of this spec called this a ranking that leaves a user-added
balancer "unrankable regardless of merit". That was wrong.
`_fallback_priority_score` (`balancer_selection.py:30-38`) converts list
position into a small *additive bonus*: 0.24 for first place down to 0.06,
0.04 for a balancer not in the list, and 0.0 when the chromosome's list is
empty (all of chromosome 4). The total score is dominated by breakpoint
assessment (up to ~0.84) plus 0.35x marker stability, and the final sort key
is `(-score, symbol)`.

So a user-added balancer with good breakpoints already beats listed ones
today. The real defect is a ~0.2 handicap it can never shed, and the fact
that a lab cannot express "we prefer this balancer" at all. That is a
genuine gap, but a much smaller one than claimed, and it changes what the
fix should look like.

### K2. The Tb / TM6B stability rule

`flymanager/utils/constraints/marker_stability.py:40`:

```python
if label == "Tb" and resolved_balancer in {"TM6B", "TM6"}:
    score = min(score, 0.35)
    notes.append("TM6B explicitly keeps Tb because the marker can revert ...")
```

The same function, ten lines earlier, reads `sorting.stabilityScore` and
`sorting.notes` from the catalog. The data mechanism for exactly this rule
already exists; this one rule is expressed as an `if` on two magic strings.
It is also the only context-dependent stability rule in the codebase — the
score depends on which balancer carries the marker — which is why it did not
fit the flat `stability[label]` table and got hardcoded instead.

### K3. Alias tables in `image_library`

`flymanager/utils/phenotypes/image_library.py:7-13`:

```python
EPISTASIS_IMAGE_ALIASES = {"epistasis:w_mini_white_rescue": [...]}
BODY_PART_ALIASES = {"wing": {"wing", "wings"}, ...}
```

`_marker_aliases` already folds `get_catalog()["image_aliases"]` — populated
from each definition's `imaging.aliases` — and then updates it with
`EPISTASIS_IMAGE_ALIASES`. So there are two alias sources for one lookup, one
editable through the UI and one not. Slice B's design explicitly noted
`BODY_PART_ALIASES` as staying hardcoded and deferred it; this is that
deferral coming due.

## Design

### K1 — `sorting.preferenceBonus`

Add an optional number in 0..0.25 to the `sorting` section of a balancer
definition, named for what it actually is: the additive bonus
`_fallback_priority_score` returns today. `balancer_selection.py` reads it
from the compiled snapshot; a balancer without one gets 0.04, matching
today's treatment of an unlisted balancer. `DEFAULT_BALANCER_PRIORITY` and
`_fallback_priority_score` are then deleted.

An earlier draft proposed an opaque rank integer "seeded in tens" with an
alphabetical fallback. Both details were wrong. The current bonus is a
function of index *within a chromosome*, so one flat sequence across all four
lists would not reproduce it; and the alphabetical fallback was misdirection,
because the sort key is already `(-score, symbol)` and therefore alphabetical
for every tie, not just unranked ones.

Seeding is mechanical: each shipped balancer takes the exact bonus
`_fallback_priority_score` returns for it today, so the constant's behaviour
is preserved by construction rather than by a translation scheme that has to
be argued. Chromosome 4's empty list becomes an explicit 0.0.

The catalog signature covers every definition field except audit metadata, so
this changes the signature — correctly, since it changes solver output.

### K2 — context-dependent stability entries

Extend `sorting` with an optional list:

```json
"sorting": {
  "stabilityScore": 0.6,
  "contextualStability": [
    {"whenBalancer": ["TM6B", "TM6"], "maxScore": 0.35,
     "note": "TM6B explicitly keeps Tb because the marker can revert at high frequency."}
  ]
}
```

`compile_catalog` indexes these **independently of** the flat `stability`
table. That separation is required, not incidental: today a `stability` entry
is only created when `stabilityScore is not None`, so indexing contextual
rules inside it would silently discard a cap on a definition that has a cap
but no base score. `marker_stability.py` applies them where the hardcoded
`if` sits today, in declaration order, each capping the score and appending
its note, after the fallback to `scoring_confidence` has run. The `Tb`
definition in the shipped catalog carries the rule above; the `if` is
deleted.

`whenBalancer` matches the **raw** balancer symbol, not one canonicalized
through `balancer_aliases`. This is bug-compatible with today's
`resolved_balancer in {"TM6B", "TM6"}` and is chosen deliberately: routing it
through the alias map would be a behaviour change smuggled in under a
refactor. If canonicalization is wanted, it is its own change with its own
test.

Note the shipped `Tb` definition already carries a reversion note in
`sorting.notes`, so a `Tb` on `TM6B` emits two near-identical notes. That is
today's behaviour and is preserved; the test asserts both, so a later
"cleanup" that merges them fails loudly rather than quietly changing output.

Only `maxScore` is supported — a cap, not an arbitrary override. Every rule
of this kind that exists today is a cap, and a cap composes safely with the
base score in a way a replacement does not.

### K3 — body-part synonyms only

**The epistasis half is dropped.** `EPISTASIS_IMAGE_ALIASES` is keyed by
`epistasis:w_mini_white_rescue`, which is not a catalog definition and never
was: that marker is synthesized at runtime in `phenotypes/epistasis.py:69`,
and `compile_catalog` keys `image_aliases` by a definition's phenotype key,
so there is nothing to hang the alias on. Moving it would mean inventing a
definition for a synthetic marker — new parser-visible surface, a signature
change, and a real risk of the alias silently ceasing to match. The constant
stays where it is, with a comment explaining why it cannot move.

A related correction: an earlier draft asserted that K3 "does not affect the
signature". That is false. `_canonical_definition` strips only
`SIGNATURE_EXCLUDED_FIELDS` — `_id`, the audit timestamps, `origin`,
`overridesShipped` — so `imaging` is hashed like everything else. Any change
to a definition's `imaging.aliases` invalidates every cache. The rule is
"everything but audit metadata is in the signature", not "sorting is in and
imaging is out".

`BODY_PART_ALIASES` is different in kind: it is a synonym table over body
parts (`wing`/`wings`), not marker knowledge, and it is consulted
symmetrically in `_body_part_matches`. Moving it per-definition would
duplicate it across every marker sharing a body part. It moves instead to a
single `data/markers/body_parts.json` alongside the catalog, loaded by the
same loader, so it is data rather than code and the marker form's body-part
combobox (specified separately) can offer exactly this vocabulary.

Body-part synonyms live outside any definition, so moving them to a data file
changes no definition and therefore no signature. That must be verified by
tracing every use of `_body_part_matches` before implementation, not assumed:
the claim is that body parts gate image matching only.

## Validation

Both new fields are user-supplied through the marker API and are consumed
numerically, by the crossing solver and the stability scorer respectively.
`validate_definition` must reject:

- `sorting.preferenceBonus` that is not a number, is a bool, or falls outside
  0..0.25.
- `sorting.contextualStability` that is not a list; an entry that is not an
  object; a `whenBalancer` that is not a list of strings; a `maxScore` that is
  not a number in 0..1; a `note` that is not a string.

This matters more than it looks. Until commit `de69c19`, `sorting` was not
validated at all while `compile_catalog` called `float()` on
`stabilityScore`, so any logged-in user could POST a definition that made
catalog compilation raise — breaking every catalog read for everyone. That
hole is now closed for `stabilityScore`; adding two more numerically-consumed
fields without validating them would reopen it in a new place.

Both fields also need registering in `MARKER_FIELD_SPECS`
(`flymanager/utils/phenotypes/marker_fields.py`), or they are invisible and
uneditable in the marker UI — which would defeat this spec's entire stated
goal of letting a lab express these things. `preferenceBonus` is a plain
numeric field; `contextualStability` is a repeating group and is the first
one the form has needed, so it may reasonably be admin-only in a first pass.

## Migration and blast radius

K1 and K2 change the compiled snapshot and therefore
`compute_marker_catalog_signature`.

An earlier draft said this invalidation runs "through the existing
scoped-rebuild path that a marker edit already triggers". That is wrong.
`rebuild_after_marker_change` is invoked only from the marker-edit route via
the job queue; a deploy that changes shipped `catalog.json` triggers nothing
at all. Caches simply fail the strict signature check
(`predictor.py:454`) and are recomputed lazily, per record, when someone next
views them.

So the choice is explicit: either accept lazy recomputation, or run a forced
backfill once at deploy. The dataset is small — the production mirror holds
113 stocks and 26 crosses — so a forced rebuild is a matter of minutes and
is the better option, because it moves the cost off the first user to open
each page. The earlier draft's framing of this as a large blast radius was
overstated.

K3 as now scoped changes no definition and no signature.

## Testing

- For every shipped balancer, the seeded `sorting.preferenceBonus` equals
  `_fallback_priority_score`'s current return value, and full solver output
  is identical before and after the constant is deleted. The current
  behaviour becomes an assertion before anything is removed.
- A user-added balancer with a bonus scores with it; one without gets 0.04,
  the same as an unlisted balancer today.
- The `Tb` + `TM6B` case returns the same score and note through
  `contextualStability` as it does through the hardcoded `if` today.
- A marker with no `contextualStability` is unaffected.
- Body-part synonym matching is unchanged after the table moves to data, and
  a malformed `body_parts.json` fails loudly at startup like the catalog does.
- The catalog signature changes across K1+K2 and not at all across K3.
- `tests/test_image_library_catalog_aliases.py:27`, which asserts
  `EPISTASIS_IMAGE_ALIASES` exists, stays green: the constant is no longer
  being moved.

## Risks

- **A full cache rebuild** is the cost of K1 and K2. It is the documented,
  exercised path for any catalog edit, but this invalidates everything at
  once rather than a scoped subset.
- **`contextualStability` is new schema surface** and could grow into a rules
  engine. The `maxScore`-only constraint is deliberate and should be defended
  in review; if a rule appears that needs more, that is a design conversation,
  not a field to add quietly.
- **K3's body-part file** introduces a second data file next to the catalog.
  It is loaded by the same loader and validated the same way, but it is one
  more thing that can be malformed at startup; it fails hard like the catalog
  does rather than degrading.
- **The `stability` table is keyed by display label**, so two definitions
  sharing a label collide silently, last writer winning by dict order.
  `contextualStability` inherits that flaw. This spec does not fix it, but a
  validation warning on duplicate display labels would be cheap and is worth
  considering while this code is open.
- **K1 is the smallest-value item here** now that its motivation has been
  corrected to a ~0.2 handicap rather than unrankability. It is still worth
  doing — a lab genuinely cannot express a balancer preference today — but if
  scope has to be cut, cut K1 before K2.
