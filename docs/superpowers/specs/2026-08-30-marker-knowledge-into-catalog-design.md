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
catalog schema and therefore the prediction signature, which triggers a
phenotype cache rebuild — that is the reason the two are separate, and the
reason this one goes second.

## Background (verified against current code at df4b8c2)

Slice A moved the marker dictionaries out of Python and into
`data/markers/catalog.json` plus the `marker_definitions` overlay. The audit
confirms that resolution itself is now genuinely single-mechanism: every
consumer reads through the `visual_markers` accessors over the compiled
snapshot, and no route or template reimplements marker lookup.

Three pockets of marker knowledge did not make that move. Each one is a place
where the catalog's own mechanism exists and one rule bypasses it.

### K1. Balancer preference order

`flymanager/utils/constraints/_shared.py:10`:

```python
DEFAULT_BALANCER_PRIORITY = {
    1: ["FM7c", "FM7a", "FM7", "FM3"],
    2: ["CyO", "SM6a", "SM5", "SM1"],
    3: ["TM3", "TM6B", "TM6", "TM2", "TM1"],
    4: [],
}
```

Read by `constraints/balancer_selection.py`, which iterates
`get_balancer_metadata_map()` — the catalog — and then ranks the result with
this hardcoded list. A balancer added through the marker UI is therefore
known to the parser and the predictor but unrankable by the crossing
constraint solver: it sorts after every listed balancer regardless of merit.
The catalog has no field for this at all.

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

### K1 — `sorting.balancerPriority`

Add an optional integer to the `sorting` section of a balancer definition:
lower sorts first, absent means unranked. `balancer_selection.py` reads it
from the compiled snapshot and falls back to alphabetical for unranked
balancers, replacing `DEFAULT_BALANCER_PRIORITY` entirely.

The shipped catalog is seeded with the current list's order (FM7c=10,
FM7a=20, … in tens, leaving room to insert), so behaviour is unchanged on
day one and the constant is deleted rather than duplicated.

`sorting` already participates in the prediction signature, so this is a
signature-affecting change. That is correct: changing which balancer the
solver prefers changes its output.

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

`compile_catalog` indexes these alongside the existing flat `stability` table.
`marker_stability.py` applies them where the hardcoded `if` sits today, in
declaration order, each capping the score and appending its note. The `Tb`
definition in the shipped catalog carries the rule above; the `if` is
deleted.

Only `maxScore` is supported — a cap, not an arbitrary override. Every rule
of this kind that exists today is a cap, and a cap composes safely with the
base score in a way a replacement does not.

### K3 — one alias source

`EPISTASIS_IMAGE_ALIASES` becomes `imaging.aliases` on the definition it
describes, and `_phenotype_image_aliases` drops its `update()` call.

`BODY_PART_ALIASES` is different in kind: it is a synonym table over body
parts (`wing`/`wings`), not marker knowledge, and it is consulted
symmetrically in `_body_part_matches`. Moving it per-definition would
duplicate it across every marker sharing a body part. It moves instead to a
single `data/markers/body_parts.json` alongside the catalog, loaded by the
same loader, so it is data rather than code and the marker form's body-part
combobox (specified separately) can offer exactly this vocabulary.

Body-part synonyms affect image matching only, never prediction, so this part
is deliberately kept out of the signature.

## Migration and blast radius

K1 and K2 change the compiled snapshot's content and therefore
`compute_marker_catalog_signature`. Every materialised phenotype cache is
invalidated once, through the existing scoped-rebuild path that a marker edit
already triggers — this is a normal catalog change, just a large one, and the
rebuild machinery is the same. It should be applied as a single deploy rather
than piecemeal so the rebuild happens once.

K3 does not affect the signature.

## Testing

- Seeding the shipped `sorting.balancerPriority` values reproduces
  `DEFAULT_BALANCER_PRIORITY`'s exact ordering for the balancers it listed —
  the constant's current behaviour becomes an assertion before it is deleted.
- A user-added balancer with a priority ranks where its priority says, and
  one without a priority ranks after all ranked balancers, deterministically.
- The `Tb` + `TM6B` case returns the same score and note through
  `contextualStability` as it does through the hardcoded `if` today.
- A marker with no `contextualStability` is unaffected.
- `EPISTASIS_IMAGE_ALIASES`' current matches are reproduced through
  `imaging.aliases`; `tests/test_image_library_catalog_aliases.py:27` asserts
  the constant's existence and is rewritten.
- Body-part synonym matching is unchanged after the table moves to data.
- The catalog signature changes exactly once across K1+K2 and not at all
  across K3.

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
