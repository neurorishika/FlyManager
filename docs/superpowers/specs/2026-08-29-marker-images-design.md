# Marker Image Store Design (Slice B)

Date: 2026-08-29
Status: approved design, ready for implementation planning

## Goal

Let users attach images to marker definitions, and put the 254 shipped
phenotype reference images under the same model. One image-entry shape, one
matching rule, one serving route, one storage backend.

This is slice B of three. Slice A (the marker definition store,
`docs/superpowers/specs/2026-08-17-marker-definition-store-design.md`, merged
`697ab49`) reserved `imaging.images[]` as this slice's extension point. This
design deliberately leaves that field empty and links images to markers from
the image side instead; the reasoning is in Part 2.

Slice C (binary expression systems) is unaffected by this work.

## Background (verified against current code at 697ab49)

Today's phenotype image library is filesystem-only and has no write path:

- `flymanager/utils/phenotypes/image_library.py` matches markers to images by
  fuzzy scoring over two sources: `data/phenotype_images/manifest.json` (254
  entries, tracked in git) and a recursive filesystem scan, gated by
  `BODY_PART_ALIASES`. Manifest entries score above scanned ones, and scanned
  entries fall back to filename-stem matching.
- Slice A already replaced `PHENOTYPE_IMAGE_ALIASES` with
  `get_catalog()["image_aliases"]`. `BODY_PART_ALIASES` stays hardcoded --- it
  is a vocabulary of anatomical synonyms, not marker data.
- `EPISTASIS_IMAGE_ALIASES` (`image_library.py:16`) is minted at runtime for
  the mini-white-rescues-white rule and has no marker definition behind it. It
  must keep working.
- Serving is `GET /stock/phenotype_image/<path:relative_path>`
  (`stock.py:210`), which resolves a path under
  `PHENOTYPE_IMAGE_LIBRARY_PATH` and guards against traversal.
- **No image upload path exists anywhere in the app.** `routes/data.py` uploads
  `.xlsx` only. `MAX_CONTENT_LENGTH` is 8 MB; `UPLOAD_FOLDER` is `data/uploads`.
- `tests/mongo_fakes.py` has `FakeCollection`/`FakeDatabase` but **no GridFS
  support**. This shapes Part 1.

Measured facts that drive the design (re-measurable with the seed builder):

| Fact | Value |
|---|---|
| Shipped library today | 254 files, all PNG, 119.8 MB, mean 470 KB |
| Same set at WebP q75, fit 1024x1024, EXIF stripped | **4.49 MB total, mean 17 KB, p95 28 KB, max 77 KB** |
| Reduction | ~27x |

That measurement is why migrating the shipped library into the database is
affordable at all: the entire library costs less in Mongo than one of today's
PNGs costs on disk.

## Decisions (confirmed with user)

- **Storage:** GridFS, for both shipped and uploaded bytes. Backup is the
  discriminator --- see "Why GridFS" below.
- **Unification:** the filesystem library is *converted into entries*, not kept
  on a parallel path. One entry model, one scorer, one route.
- **Normalization:** every image entering the system, shipped or uploaded, is
  re-encoded to WebP quality 75, fit within 1024x1024, EXIF stripped. Uploads
  are capped at 2 MB raw.
- **Visibility:** same global-immediate rule as marker definitions. An uploaded
  image is live for everyone on the next image-catalog refresh.
- **Signature:** an image change does **not** move `markerCatalogSignature` and
  does **not** trigger a rebuild. Images affect display, not predictions.

### Why GridFS rather than the filesystem

`BACKUP_AND_RECOVERY.md:14-105` documents two *independent* archives: Mongo
dumps (`scripts/mongo-backup.sh`) and deployment-state archives
(`scripts/state-backup.sh`, which covers `data/`). Filesystem storage would put
image bytes in the state archive while entry metadata lives in Mongo, so
restoring one without the other yields dangling references or orphan blobs.
GridFS puts bytes and metadata in a single consistency and backup domain, rides
the existing two-member replica set, and needs no new infrastructure. Object
storage was rejected as disproportionate for a single-node Synology deployment.

### Why images do not move the catalog signature

`compute_marker_catalog_signature` hashes canonicalized overlay documents. Had
image metadata gone into `imaging.images[]` on the marker definition, as slice
A's extension point anticipated, every upload would mutate that document and
move the signature automatically --- triggering a full-collection rebuild for a
change that cannot affect any prediction.

Excluding `imaging.images` from the hash would work but leaves a load-bearing
line that a later change can silently break. Storing image metadata in a
separate collection makes the property structural instead: the signature input
never contains image data because image data is not in those documents.

`imaging.images[]` therefore stays permanently empty. It is retained on the
envelope rather than removed, because removing it would change slice A's
document shape for no benefit.

## Architecture

### Part 1 --- The storage layer

New module `flymanager/utils/phenotypes/image_store.py` defining a four-method
interface:

```
put(data: bytes, *, content_type: str) -> str    # returns storage id
open(storage_id) -> file-like                    # streaming, not read-all
delete(storage_id) -> None
exists(storage_id) -> bool
```

Two implementations:

- `GridFSImageStore(db, bucket_name="marker_images")` --- production.
- `MemoryImageStore()` --- a dict, for tests.

This is the only module that names GridFS. `FakeDatabase` never needs to grow a
GridFS emulation, and the binary layer is testable without Mongo. Route and
job code takes a store instance rather than reaching for `gridfs` directly.

### Part 2 --- The entry model

New Mongo collection `marker_images`, unique index on `imageId`, secondary
index on `match.markerKeys` and on `sha256`.

```
{
  imageId: "img_<sha256[:16]>",     # stable identity, also the URL segment
  storageId: "<gridfs id>",
  sha256: "<hex>",                  # of the NORMALIZED bytes
  contentType: "image/webp",
  bytes: 17342, width: 1024, height: 768,

  match: {
    markerKeys: [],                 # exact binding to marker_definitions.Key
    aliases:    [],                 # fuzzy tokens (how shipped entries match)
    bodyPart:   "wing"              # gated by BODY_PART_ALIASES
  },
  display: {
    label, caption, credit, provenance,
    priority:  2,                   # from the shipped manifest; uploads get 3
    sortOrder: 0                    # within one marker
  },

  origin: "shipped" | "user",
  UploadedBy, UploadedAt
}
```

**Identity is content-derived, so entries deduplicate.** Because `imageId`
comes from the normalized bytes' hash, uploading the same image against a
second marker resolves to an existing row. The write path therefore **upserts**:
if `imageId` already exists, append the marker key to `match.markerKeys` and
leave the stored bytes, `display`, and `UploadedBy` alone; only insert when the
id is new. The same rule makes a re-upload of an existing shipped image a
no-op rather than a duplicate or a 409.

The link to a marker runs image -> marker via `match.markerKeys`, not marker ->
image. Besides keeping the signature clean (above), this is the direction the
data actually has: one image commonly illustrates several markers, and the
shipped library has 254 images against a far larger marker catalog.

### Part 3 --- Seed and migration

`data/phenotype_images/` (120 MB of PNG plus `manifest.json`) is **deleted from
git** and replaced by `data/markers/images/`:

- 254 normalized `.webp` files, 4.49 MB total
- `index.json` --- one entry record per file, minus `storageId` (assigned at
  load time)

`scripts/build_marker_image_seed.py` produces this directory from the current
`data/phenotype_images/` tree. It is a one-time developer tool, run once and
committed; it is not part of the runtime. It must be deterministic so the
generated `index.json` is reviewable in the diff.

At startup, `load_image_seed(db, store)` inserts any seed entry whose `sha256`
is absent from `marker_images`, streaming its bytes into GridFS first. Keying
on the content hash makes it idempotent, safe to re-run, and safe on a fresh
deploy or an existing one alike. This mirrors how `catalog.json` seeds
`marker_definitions` in slice A.

**The alias conversion is the substantive migration.** Today a scanned entry
can match through its *filename stem* at runtime (`image_library.py:190`,
`allow_stem_fallback`). The seed builder resolves those stems into explicit
`match.aliases` at build time, so the runtime has one rule --- score against
declared aliases --- with no stem-fallback branch. This is the highest-risk
part of the slice: it is where a marker could silently lose the image it shows
today. Part 8 covers how it is pinned down.

### Part 4 --- Compilation and freshness

New module `flymanager/utils/phenotypes/image_catalog.py`, mirroring
`marker_catalog.py`:

- `compile_image_catalog(documents)` --- indexed snapshot, by marker key and by
  alias.
- `get_image_catalog()` --- process-global snapshot. **Never touches Mongo**,
  for the same reason the marker catalog does not: matching runs on the
  db-less resolution path.
- `refresh_image_catalog(db, *, force=False)` --- recompiles if the revision
  moved.

The settings singleton gains `markerImageRevision`, bumped on every image
write, probed by a `before_request` hook at most once per 30 seconds. It is
**separate from `markerCatalogRevision`** so an upload never forces a marker
snapshot recompile.

Only bytes come from GridFS, and only in the serving route. Metadata matching
stays entirely in-process.

### Part 5 --- Matching

`select_phenotype_reference_images(markers, *, limit=6)` and
`select_prediction_reference_images(prediction, *, limit=6)` keep their names
and their callers (`cross.py:95,110,141` and `stock.py:60,69`). The `base_dir`
parameter is dropped --- there is no directory any more.

One scorer over catalog entries:

1. **Exact key match** --- the marker's `Key` is in `entry.match.markerKeys`.
   Scores above any alias match.
2. **Alias match** --- scored as today, using the marker aliases
   `_marker_aliases` already derives (which include `get_catalog()`'s
   `image_aliases`, plus `EPISTASIS_IMAGE_ALIASES`).
3. **Body-part gate** --- unchanged, still `BODY_PART_ALIASES`.
4. Ties broken by `(display.priority, display.sortOrder, imageId)` --- a total
   order, never dependent on set or dict iteration.

"An upload beats a fuzzy library match" is therefore a consequence of rule 1
outranking rule 2, not a special case in the code.

Each returned match carries a URL built from `imageId`, replacing today's
relative path.

### Part 6 --- Upload, serving, permissions

- `POST /markers/<key>/images` --- multipart upload. Accepts png, jpg, jpeg,
  gif, webp up to **2 MB** raw, enforced in the route by reading
  `request.content_length` and bounding the stream read; the app-global
  `MAX_CONTENT_LENGTH` stays 8 MB. Validation **decodes** the file with Pillow
  (`Image.open(...).verify()`, then reopen to transform) rather than trusting
  the extension or the declared content type, and enforces
  `Image.MAX_IMAGE_PIXELS` against decompression bombs. Normalizes to WebP q75,
  fit within 1024x1024, EXIF stripped. Only the normalized bytes are stored;
  the original is discarded. Sets `match.markerKeys = [key]`.
- `POST /markers/images/<image_id>/delete` --- uploader or admin.
  `origin: "shipped"` entries are admin-only.
- `GET /markers/images/<image_id>` --- `@login_required`, rate-limited to match
  the existing image route's 60/minute, streams from the store with
  `ETag: "<sha256>"` and `Cache-Control: private, max-age=31536000, immutable`.
  Immutability is safe because `imageId` derives from content: any edit
  produces a new id.
- `GET /stock/phenotype_image/<path>` is **removed**, along with
  `PHENOTYPE_IMAGE_LIBRARY_PATH` and
  `FLYMANAGER_PHENOTYPE_IMAGE_LIBRARY_PATH`.

Permissions follow slice A's marker rules and its reasoning: any logged-in user
may attach an image, and it is live for everyone immediately. Attribution is
the safety mechanism --- every image shows its uploader, and every write calls
`write_activity`.

Image writes bump `markerImageRevision`, call `write_activity`, and **do not**
enqueue `rebuild_after_marker_change`. `derive_affected_tokens` needs no
change: images introduce no new path from a genotype string to a marker
definition, so the stamping invariant from slice A is untouched.

### Part 7 --- UI

The `/markers/<key>` detail page from slice A gains an image strip: the
marker's current images in `sortOrder`, an upload control, a caption field, and
a delete action on each. Shipped images render with their `credit` and
`provenance`, and are read-only for non-admins.

Prediction views are unchanged apart from the image URLs they render.

## Data model changes

- New collection `marker_images`; unique index on `imageId`, indexes on
  `match.markerKeys` and `sha256`.
- New GridFS bucket `marker_images`.
- New tracked directory `data/markers/images/` (254 WebP + `index.json`,
  4.49 MB).
- **Deleted:** `data/phenotype_images/` and its `manifest.json` (120 MB).
- Settings singleton gains `markerImageRevision` (integer, absent means 0).
- `imaging.images[]` on marker definitions stays empty; no schema change.
- **No cache version bump and no signature change.** `PhenotypeCache` and
  `StandardizationCache` envelopes are untouched.

## Dependencies and operations

- **Adds Pillow** to `pyproject.toml`. It is the only new runtime dependency.
  The measurements above were taken with ImageMagick; Pillow encodes through
  the same libwebp, so parity is expected but **must be confirmed** by the seed
  builder's own output before the seed is committed.
- Backup needs no new machinery: GridFS is inside the database, so
  `scripts/mongo-backup.sh` already covers it, at roughly +4.5 MB per archive.
- `BACKUP_AND_RECOVERY.md:78-105` must be updated: shipped images move from the
  state archive into the Mongo archive, and the state archive shrinks by
  ~120 MB. Note the tradeoff explicitly --- shipped images are no longer purely
  reproducible from the Docker image, though the 4.49 MB seed in git means a
  fresh deploy still self-populates.

## Error handling

- A corrupt or unreadable seed file is a startup failure, matching slice A's
  treatment of a malformed shipped catalog.
- A seed entry whose bytes are already in GridFS is skipped silently; this is
  the normal restart path.
- An upload that fails decoding, exceeds 2 MB, or trips the pixel limit returns
  400 with a specific message. Nothing is written --- normalize first, store
  only on success.
- If the GridFS write succeeds but the metadata insert fails, the orphaned blob
  is deleted in the exception path. An orphan that survives a crash is inert:
  nothing references it.
- A metadata document whose `storageId` is missing from the store is skipped by
  the scorer with a logged warning, and shown as broken in the `/markers` UI.
  One bad row must not blank out a prediction view.
- Compilation failure leaves the previous snapshot in place, as in slice A.

## Testing strategy

TDD against `FakeDatabase` plus `MemoryImageStore`. Load-bearing cases:

**Normalizer**
- Bounds output: every file in the shipped set normalizes to <= 100 KB.
- Rejects a non-image with an image extension; rejects a decompression bomb;
  rejects over-2 MB input.
- Strips EXIF; converts palette/RGBA/CMYK inputs without error.
- Is idempotent: normalizing an already-normalized image is stable.

**Seed and migration**
- `load_image_seed` is idempotent --- running it twice inserts 254 rows, not
  508.
- Running it against a partially-loaded collection inserts only what is
  missing.
- Every `index.json` entry's `sha256` matches its file's bytes.

**Matching (the regression surface)**
- **Port the existing shipped-library assertions** ---
  `tests/test_phenotype_routes.py:446-530` currently pins ebony, yellow, roi,
  bc, me and wa matches against the real library. These must resolve to the
  same images through the new path. This is the primary guard on the Part 3
  alias conversion.
- Exact `markerKeys` outranks a higher-scoring alias match.
- The body-part gate still excludes cross-body-part matches.
- `EPISTASIS_IMAGE_ALIASES` still resolves the mini-white rescue key.
- Ordering is stable under `PYTHONHASHSEED=random` across repeated runs.

**Isolation from predictions**
- An image upload bumps `markerImageRevision`.
- An image upload leaves `markerCatalogSignature` **byte-identical** and
  enqueues no rebuild job.

**Routes**
- Upload requires login; delete of a shipped image requires admin; delete of a
  user image is allowed for its uploader and refused for a third party.
- The serving route returns the right `ETag` and a 404 for an unknown id.

New test files run under `PYTHONHASHSEED=random` several times, per the slice A
invariants.

## Out of scope

- Thumbnails or multiple resolutions. At a 17 KB mean the full image *is* the
  thumbnail.
- Retaining original uploaded bytes. Rejected explicitly: ~20x the storage,
  and it reintroduces the 120 MB problem the compression pass removes.
- Cropping, annotation, or any in-app editing.
- Per-user or per-lab image visibility. Images follow the marker catalog's
  global-immediate rule.
- Changing `BODY_PART_ALIASES`, epistasis rules, or anything in slice C.
