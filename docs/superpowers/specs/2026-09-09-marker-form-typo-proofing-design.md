# Marker Form Typo-Proofing Design

Date: 2026-09-09
Status: implemented (branch feature/marker-form-typo-proofing)

## Goal

Make it as hard as possible to make a typo in the marker catalog forms and
create a mess. A marker definition edited here is globally live for the whole
lab the moment it saves, and the people qualified to correct one know flies,
not schemas. Today almost every field is free text, so `Sb` becomes `sb1`,
`wing` becomes `Wings`, and nothing notices.

The design was agreed 2026-08-30 and deliberately queued behind the image
work; that work landed (`b84969b`, `749a8de`), so nothing blocks it now.

## Principle

Every field whose vocabulary already exists in the catalog becomes a
constrained control over that vocabulary. The vocabulary is derived, never
duplicated: it is read from the compiled snapshot at render time, so a marker
added yesterday is offered today without anyone maintaining a list.

Constraint is offered, not enforced, wherever enforcing would block a
legitimate edit. The point is to make the right value the easy one, not to
make an unusual one impossible.

## Part 1 — Native constrained controls

No JavaScript. `<input list>` + `<datalist>`, which degrades to a plain text
input in anything that does not support it.

| Field | Vocabulary |
|---|---|
| `payload.body_part`, `payload.overrides.body_part` | `get_body_parts()` keys |
| `payload.family` | balancer families in the catalog |
| `provenance.source` | sources already used |
| `payload.value` (alias target) | every definition Key |
| `payload.gene_stem`, `match.geneStem` | gene stems already defined |
| `whenBalancer` (contextual stability) | balancer symbols |

`payload.chromosome` becomes a real `select`: `1 (X)`, `2`, `3`, `4`. It is
stored as an int, so `options` grows from bare strings to `(value, label)`
pairs. The blank option stays — the gene-marker help text says to leave it
blank if the chromosome varies, and that is a real state.

A new `marker_vocabularies()` in `marker_fields.py` returns
`{name: [values]}`, computed from `get_catalog()` and `get_body_parts()`. It
takes no database handle, like everything else on the phenotype resolution
path. Fields name their vocabulary with a new `vocabulary=` argument; the
template looks it up.

**Decision: `body_part` offers the canonical keys only, not their synonyms.**
The synonym table exists so an image filename saying `wings` matches a marker
saying `wing`; the value stored on a marker should be the canonical one. A
lab that genuinely needs a new body part still types it.

## Part 2 — Tag inputs

`payload.default_markers`, `match.aliases`, `imaging.aliases` and
`whenBalancer` are lists that today are comma-separated text, so a stray
space or a missing comma silently makes one wrong value out of two right
ones. They become Tagify inputs.

Tagify is already vendored (4.31.3) and already used on the stock and cross
pages. It is configured with `originalInputValueFormat` so it writes plain
comma-separated text back into the original input, which means:

- the server parses it with the existing `_parse_list` and nothing changes,
- `clean_tagify_data` is not involved (that helper parses Tagify's default
  JSON shape, which this configuration avoids emitting),
- with JavaScript off, the field is exactly the textarea it is today.

**Decision: `default_markers` autocompletes but does not enforce.** A
balancer may legitimately carry a marker nobody has defined yet, and
`unresolved_balancer_markers` already reports a key that resolves to nothing
as a warning on the catalog page. Suggestion plus that existing net is the
right pair; enforcement would block real work.

`match.aliases` and `imaging.aliases` get no whitelist at all — an alias is a
novel spelling by definition.

## Part 3 — Live duplicate-Key check

The create page checks the Key as it is typed, against a small JSON endpoint,
and reports one of three states rather than two:

- **free** — nothing uses it.
- **taken by a lab marker** — a link to it; saving would be an error.
- **shipped** — saving creates a lab override of a built-in marker. This is a
  legitimate, supported path (`create_marker` documents it), so it is
  explained, not warned about.

The endpoint checks the overlay collection as well as the compiled snapshot,
because the in-process catalog can be up to one refresh interval stale and a
marker created seconds ago by another worker would otherwise read as free.

## Part 4 — A photo on the create page

Adding a marker and then having to find it again to add its photo is how
markers end up with no photo. The create form gains an optional photo and
caption.

It cannot be one request: the upload endpoint 404s without an existing
marker. So the marker is created first, and the image is attached
immediately afterwards, in the same request handler. If the marker saves and
the image is rejected, **the marker still saves** and the flash says the
photo did not attach and why.

The body of `upload_marker_image` moves into a helper both routes call, so
the two paths cannot drift.

## Testing

- Every vocabulary is derived from the catalog: adding a definition to the
  overlay makes its value appear, with no list edited anywhere.
- Chromosome posts as an int and the blank option round-trips as absent.
- A Tagify-shaped post (comma-separated) parses to the same list the plain
  textarea produced.
- The Key check reports free, taken, and shipped distinctly, and consults the
  overlay, not only the snapshot.
- Creating with a photo attaches it; creating with a rejected photo still
  creates the marker and says the photo did not attach.

## Risks

- **Datalists are advisory.** A browser will still submit a value that is not
  in the list. That is intended here, but it means the datalist is a typo
  guard, not validation, and nothing downstream may start assuming otherwise.
- **A select posts what it shows.** A stored value outside its option list --
  a chromosome of 5 set through the admin JSON editor -- would be silently
  cleared by opening and saving the marker. The select offers any such value
  back as an extra option rather than dropping it. Every shipped definition is
  in range today (1-4 or blank, audited at implementation time), so this is a
  guard, not a fix for something live.
- **The vocabulary is only as clean as the catalog.** A typo already saved
  becomes a suggestion offered to the next person. The duplicate-label warning
  added with K2 is the existing counterweight; this design does not add
  another.
