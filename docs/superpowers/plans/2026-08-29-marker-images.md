# Marker Image Store Implementation Plan (Slice B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users upload images onto marker definitions, and migrate the 254
shipped phenotype images into the same GridFS-backed store, so shipped and
uploaded images share one entry shape, one scorer and one serving route.

**Architecture:** A `marker_images` Mongo collection holds entry metadata and a
GridFS bucket holds bytes, both behind a four-method `ImageStore` interface so
tests never need GridFS. Entries are compiled into a process-global snapshot
mirroring slice A's `marker_catalog`, keeping matching database-free. Images
link to markers via `match.markerKeys` on the image, never via
`imaging.images[]` on the marker — which is what keeps image writes out of
`markerCatalogSignature`.

**Tech Stack:** Python 3.10, Flask, PyMongo + GridFS, Pillow (new dependency),
pytest.

**Spec:** `docs/superpowers/specs/2026-08-29-marker-images-design.md`

## Verified Facts About This Codebase

Every line below was checked against the code at `697ab49`. Do not re-derive
them, and do not assume anything resembling them without checking.

- **The markers blueprint has NO url_prefix.** `__init__.py:299` is
  `app.register_blueprint(markers.bp)`, and `markers.py:27` is
  `Blueprint("markers", __name__)`. Slice A's rules therefore spell the prefix
  out: `@bp.get("/markers")`, `@bp.get("/markers/<path:key>")`. **Every new
  rule must begin with `/markers`.** A rule of `/<path:key>/images` would still
  match `/markers/Sb[1]/images` — because `path:` spans slashes — binding the
  image to the nonexistent key `markers/Sb[1]`. Silent corruption, not a 404.
- **`base_dir=` IS passed at all five call sites**: `cross.py:97,112,143` and
  `stock.py:62,71`, each as
  `base_dir=current_app.config.get("PHENOTYPE_IMAGE_LIBRARY_PATH")`. Removing
  the parameter without editing these is a `TypeError` in every prediction view.
- **There is no `tests/conftest.py`.** No `client`, `logged_in_client` or
  `seeded_image` fixture exists anywhere. `test_phenotype_routes.py` builds
  clients inline and gets a CSRF token via a module-local
  `_get_authenticated_csrf_token(client)` (line 272).
- **CSRF is enforced globally**: `__init__.py:179` sets
  `WTF_CSRF_CHECK_DEFAULT = True`, `security.py:9` installs `CSRFProtect()`.
  Every POST — in tests and in templates — needs a token or it 400s.
- **`FakeCollection.update_one` ignores `$inc`.** `mongo_fakes.py:196-207`
  applies only `$set`. `$inc` works solely through `find_one_and_update`
  (`mongo_fakes.py:209+`, which accepts both `"AFTER"` and `ReturnDocument.AFTER`).
- **`write_activity(user, activity, db)`** — `utils/mongo/activity.py:3`. Three
  positional arguments, `db` **last**, no separate event-type field.
- **The revision bump lives in `utils/mongo/marker_definitions.py:40-63`**, not
  in `marker_catalog.py`. It uses `find_one_and_update` with `$inc`,
  `upsert=True`, `ReturnDocument.AFTER`, and raises if the result is malformed.
- **`refresh_catalog` serializes read-compile-install on a dedicated
  `_REFRESH_LOCK`** (`marker_catalog.py:356-383`), explicitly so a slow compile
  of an older revision cannot overwrite a newer snapshot. Production is
  `gunicorn --workers 1 --threads 12`, so this matters.
- **`catalog.json` payload keys are snake_case**: `body_part`, `chromosome`,
  `display_label`, `dominance`, `effect`, `phenotype_key`,
  `scoring_confidence`. There is no `geneStem` or `gene_stem` key.
- **The `before_request` probe to extend is `refresh_marker_catalog_if_stale`**
  at `__init__.py:266-277`; it calls `maybe_refresh_catalog(db)` and swallows
  exceptions so a refresh failure never fails a request. Jobs call
  `refresh_catalog` at `jobs/tasks.py:33`.
- **Already correct, do not "fix":** `FakeDatabase.__getattr__`
  (`mongo_fakes.py:301`) auto-creates unseeded collections; `find` supports
  projections (line 166); `ensure_mongo_indexes` is at `utils/mongo/db.py:51`;
  `limiter` is already imported in `markers.py:17`; the endpoints
  `markers.marker_detail` and `markers.marker_catalog` exist.

## Global Constraints

- **Normalization contract, applied to every image without exception:** WebP,
  quality **75**, fit within **1024x1024** (never upscale), EXIF stripped.
  Uploads capped at **2 MB** raw input. Only normalized bytes are stored.
- **`markerCatalogSignature` must not move and no rebuild may be enqueued** on
  any image write. `derive_affected_tokens` is not modified by this slice.
- **`imaging.images[]` on marker definitions stays empty.** Never write to it.
- **`get_image_catalog()` must never touch Mongo** — matching runs on the
  database-free resolution path, exactly as `get_catalog()` does.
- **Sort keys need a total order.** Never rely on set or dict iteration; run
  new test files under `PYTHONHASHSEED=random` several times.
- **Genotype strings in fixtures need four chromosome fields:**
  `"w[1118]; CyO/Sp; +; +"`, never `"w[1118]; CyO/Sp"`.
- **Test command** (from the worktree root, Mongo container running):

  ```bash
  export MONGO_URI="mongodb://127.0.0.1:27017/?directConnection=true"
  export MONGO_DB_NAME="flymanager_test_mib" ENABLE_SCHEDULER=0 \
         SECRET_KEY=test-secret-key MAIL_SUPPRESS_SEND=1
  /Users/neurorishika/Projects/Rockefeller/Ruta/FlyManager/.venv/bin/python \
    -m pytest tests/ -q -p no:cacheprovider --ignore=tests/new_feature_exploration
  ```

- **Baseline is 15 pre-existing failures, 586 passing.** Do not attempt to fix
  them; confirm your change does not add to the count.

---

## File Structure

| File | Responsibility |
|---|---|
| `flymanager/utils/phenotypes/image_store.py` | **Create.** The only module that names GridFS. `ImageStore` interface, `GridFSImageStore`, `MemoryImageStore`. |
| `flymanager/utils/phenotypes/image_normalize.py` | **Create.** Pillow decode, validate, re-encode. No Mongo, no Flask. |
| `flymanager/utils/phenotypes/image_catalog.py` | **Create.** Entry documents to process-global snapshot; revision probe. Mirrors `marker_catalog.py`. |
| `flymanager/utils/phenotypes/image_library.py` | **Rewrite.** Keeps `select_phenotype_reference_images` / `select_prediction_reference_images` and the ported `_score_entry`; loses all filesystem access. |
| `scripts/build_marker_image_seed.py` | **Create.** One-time developer tool producing `data/markers/images/`. Not runtime. |
| `flymanager/utils/phenotypes/image_seed.py` | **Create.** `load_image_seed(db, store)`, idempotent, sha256-keyed. |
| `flymanager/app/routes/markers.py` | **Modify.** Upload, delete and serve routes. |
| `flymanager/app/__init__.py` | **Modify.** Drop `PHENOTYPE_IMAGE_LIBRARY_PATH`, add the image-revision `before_request` probe, run the seed loader. |
| `data/markers/images/` | **Create** (254 webp + `index.json`). `data/phenotype_images/` is **deleted**. |

Tasks are ordered so nothing is deleted until its replacement passes tests.
Task 8 Step 9 is the only irreversible step and has its own gate.

---

### Task 1: `ImageStore` interface and both implementations

**Files:**
- Create: `flymanager/utils/phenotypes/image_store.py`
- Test: `tests/test_marker_image_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class ImageStore` with `put(data: bytes, *, content_type: str) -> str`,
  `open(storage_id: str) -> BinaryIO`, `delete(storage_id: str) -> None`,
  `exists(storage_id: str) -> bool`. Concrete: `GridFSImageStore(db, bucket_name="marker_images")`,
  `MemoryImageStore()`. `open()` raises `ImageNotFound` for a missing id.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from flymanager.utils.phenotypes.image_store import (
    ImageNotFound,
    MemoryImageStore,
)


def test_memory_store_round_trips_bytes():
    store = MemoryImageStore()
    storage_id = store.put(b"webp-bytes", content_type="image/webp")
    assert store.exists(storage_id)
    assert store.open(storage_id).read() == b"webp-bytes"


def test_memory_store_delete_removes_object():
    store = MemoryImageStore()
    storage_id = store.put(b"x", content_type="image/webp")
    store.delete(storage_id)
    assert not store.exists(storage_id)
    with pytest.raises(ImageNotFound):
        store.open(storage_id)


def test_memory_store_ids_are_unique_for_identical_bytes():
    store = MemoryImageStore()
    first = store.put(b"same", content_type="image/webp")
    second = store.put(b"same", content_type="image/webp")
    assert first != second
```

Note the third test: the *store* does not deduplicate. Deduplication is the
catalog's job, keyed on `sha256` (Task 6). Keeping the store dumb means it has
one responsibility.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_marker_image_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'flymanager.utils.phenotypes.image_store'`

- [ ] **Step 3: Write minimal implementation**

```python
import io
import uuid

import gridfs


class ImageNotFound(LookupError):
    """Raised when a storage id has no bytes behind it."""


class ImageStore:
    def put(self, data, *, content_type):
        raise NotImplementedError

    def open(self, storage_id):
        raise NotImplementedError

    def delete(self, storage_id):
        raise NotImplementedError

    def exists(self, storage_id):
        raise NotImplementedError


class MemoryImageStore(ImageStore):
    def __init__(self):
        self._objects = {}

    def put(self, data, *, content_type):
        storage_id = uuid.uuid4().hex
        self._objects[storage_id] = (bytes(data), content_type)
        return storage_id

    def open(self, storage_id):
        if storage_id not in self._objects:
            raise ImageNotFound(storage_id)
        return io.BytesIO(self._objects[storage_id][0])

    def delete(self, storage_id):
        self._objects.pop(storage_id, None)

    def exists(self, storage_id):
        return storage_id in self._objects


class GridFSImageStore(ImageStore):
    def __init__(self, db, bucket_name="marker_images"):
        self._bucket = gridfs.GridFSBucket(db, bucket_name=bucket_name)

    def put(self, data, *, content_type):
        object_id = self._bucket.upload_from_stream(
            "marker-image",
            io.BytesIO(bytes(data)),
            metadata={"contentType": content_type},
        )
        return str(object_id)

    def open(self, storage_id):
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            return self._bucket.open_download_stream(ObjectId(storage_id))
        except (gridfs.NoFile, InvalidId) as exc:
            raise ImageNotFound(storage_id) from exc

    def delete(self, storage_id):
        from bson import ObjectId
        from bson.errors import InvalidId

        try:
            self._bucket.delete(ObjectId(storage_id))
        except (gridfs.NoFile, InvalidId):
            pass

    def exists(self, storage_id):
        try:
            self.open(storage_id).close()
        except ImageNotFound:
            return False
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_marker_image_store.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Add a GridFS-backed test against the real Mongo**

`FakeDatabase` cannot emulate GridFS, so `GridFSImageStore` is covered against
the containerized Mongo the suite already requires. Append:

```python
from flymanager.utils.phenotypes.image_store import GridFSImageStore


def test_gridfs_store_round_trips_against_real_mongo():
    from flymanager.app import db

    store = GridFSImageStore(db, bucket_name="marker_images_test")
    storage_id = store.put(b"webp-bytes", content_type="image/webp")
    try:
        assert store.exists(storage_id)
        assert store.open(storage_id).read() == b"webp-bytes"
    finally:
        store.delete(storage_id)
    assert not store.exists(storage_id)


def test_gridfs_store_reports_missing_object():
    from flymanager.app import db

    store = GridFSImageStore(db, bucket_name="marker_images_test")
    assert not store.exists("000000000000000000000000")
    assert not store.exists("not-an-object-id")
```

Run: `python -m pytest tests/test_marker_image_store.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/phenotypes/image_store.py tests/test_marker_image_store.py
git commit -m "feat: add an ImageStore interface with GridFS and in-memory backends"
```

---

### Task 2: The normalizer

**Files:**
- Create: `flymanager/utils/phenotypes/image_normalize.py`
- Modify: `pyproject.toml` (add Pillow)
- Test: `tests/test_marker_image_normalize.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize_image(raw: bytes, *, max_bytes: int | None = MAX_UPLOAD_BYTES) -> NormalizedImage`.
  `max_bytes=None` disables the size gate — the seed builder needs this, since
  the shipped PNGs average 470 KB but some exceed 1 MB and the gate exists for
  untrusted uploads, not for a developer tool. Returns a dataclass with
  fields `data: bytes`, `content_type: str`, `width: int`, `height: int`,
  `sha256: str`, `bytes: int`. Raises `ImageRejected(reason)` — a `ValueError`
  subclass — for anything invalid. Constants `MAX_UPLOAD_BYTES = 2 * 1024 * 1024`,
  `MAX_DIMENSION = 1024`, `WEBP_QUALITY = 75`.

- [ ] **Step 1: Add Pillow to the project**

```bash
poetry add pillow
```

If `poetry add` is unavailable in the environment, add `pillow = "^11.0"` under
`[tool.poetry.dependencies]` in `pyproject.toml` and install it into the venv
with `/Users/neurorishika/Projects/Rockefeller/Ruta/FlyManager/.venv/bin/pip install pillow`.

- [ ] **Step 2: Write the failing test**

```python
import io

import pytest
from PIL import Image

from flymanager.utils.phenotypes.image_normalize import (
    ImageRejected,
    MAX_UPLOAD_BYTES,
    normalize_image,
)


def _png_bytes(width, height, color=(200, 30, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_normalize_outputs_webp_with_metadata():
    result = normalize_image(_png_bytes(640, 480))
    assert result.content_type == "image/webp"
    assert (result.width, result.height) == (640, 480)
    assert result.bytes == len(result.data)
    assert len(result.sha256) == 64
    assert Image.open(io.BytesIO(result.data)).format == "WEBP"


def test_normalize_downscales_to_fit_max_dimension():
    result = normalize_image(_png_bytes(4000, 2000))
    assert (result.width, result.height) == (1024, 512)


def test_normalize_never_upscales_small_images():
    result = normalize_image(_png_bytes(64, 32))
    assert (result.width, result.height) == (64, 32)


def test_normalize_is_idempotent():
    once = normalize_image(_png_bytes(640, 480))
    twice = normalize_image(once.data)
    assert twice.sha256 == once.sha256


def test_normalize_converts_rgba_and_palette_inputs():
    for mode in ("RGBA", "P", "L"):
        buffer = io.BytesIO()
        Image.new(mode, (32, 32)).save(buffer, format="PNG")
        assert normalize_image(buffer.getvalue()).content_type == "image/webp"


def test_normalize_strips_exif():
    buffer = io.BytesIO()
    image = Image.new("RGB", (32, 32))
    exif = image.getexif()
    exif[271] = "SecretCamera"
    image.save(buffer, format="JPEG", exif=exif)
    result = normalize_image(buffer.getvalue())
    assert not Image.open(io.BytesIO(result.data)).getexif()


def test_normalize_rejects_non_image_bytes():
    with pytest.raises(ImageRejected):
        normalize_image(b"this is not an image")


def test_normalize_rejects_oversized_input():
    with pytest.raises(ImageRejected):
        normalize_image(b"\x00" * (MAX_UPLOAD_BYTES + 1))


def test_normalize_rejects_empty_input():
    with pytest.raises(ImageRejected):
        normalize_image(b"")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_marker_image_normalize.py -q`
Expected: FAIL — `ModuleNotFoundError: ... image_normalize`

- [ ] **Step 4: Write the implementation**

```python
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 1024
WEBP_QUALITY = 75
CONTENT_TYPE = "image/webp"

# Guard against decompression bombs: 1024x1024 output needs far less than this,
# and Pillow's own default is higher than we want for user uploads.
MAX_INPUT_PIXELS = 50_000_000


class ImageRejected(ValueError):
    """Raised when input is not a usable image."""


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    content_type: str
    width: int
    height: int
    sha256: str
    bytes: int


def normalize_image(raw, *, max_bytes=MAX_UPLOAD_BYTES):
    raw = bytes(raw or b"")
    if not raw:
        raise ImageRejected("empty upload")
    if max_bytes is not None and len(raw) > max_bytes:
        raise ImageRejected(
            f"image is larger than {max_bytes // (1024 * 1024)} MB"
        )

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageRejected("file is not a readable image") from exc

    # verify() consumes the file object, so reopen to transform.
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_INPUT_PIXELS:
                raise ImageRejected("image has too many pixels")
            image = image.convert("RGB")
            image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=6)
            width, height = image.size
    except ImageRejected:
        raise
    except (OSError, ValueError) as exc:
        raise ImageRejected("image could not be re-encoded") from exc

    data = buffer.getvalue()
    return NormalizedImage(
        data=data,
        content_type=CONTENT_TYPE,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
    )
```

`Image.thumbnail` never upscales, which is why it is used rather than
`resize` — that is what makes the "never upscales" test pass for free.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_marker_image_normalize.py -q`
Expected: PASS (9 passed)

- [ ] **Step 6: Verify Pillow matches the measured ImageMagick output**

The spec's 4.49 MB figure was measured with ImageMagick. Confirm parity before
the seed is built on it:

```bash
python - <<'PY'
import glob, os
from flymanager.utils.phenotypes.image_normalize import normalize_image

sizes = []
for path in glob.glob("data/phenotype_images/**/*.png", recursive=True):
    sizes.append(normalize_image(open(path, "rb").read(), max_bytes=None).bytes)
print(len(sizes), "images", round(sum(sizes) / 1e6, 2), "MB",
      "mean", round(sum(sizes) / len(sizes) / 1024), "KB",
      "max", round(max(sizes) / 1024), "KB")
PY
```

Expected: 254 images, total in the 4-7 MB range, max under 100 KB. If the total
exceeds 10 MB, stop and report — the storage argument in the spec depends on
this number.

The `max_bytes=None` is deliberate: some shipped PNGs exceed the 2 MB upload
gate, which exists for untrusted input rather than for a developer tool.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml poetry.lock flymanager/utils/phenotypes/image_normalize.py tests/test_marker_image_normalize.py
git commit -m "feat: add the marker image normalizer"
```

---

### Task 3: Scoring parity harness

This task writes the safety net **before** anything that could break it, per
the spec's testing strategy. It captures today's behavior as data.

**Files:**
- Create: `tests/fixtures/image_scoring_baseline.json`
- Create: `tests/test_marker_image_scoring_parity.py`
- Create: `scripts/capture_image_scoring_baseline.py`

**Interfaces:**
- Consumes: today's `select_phenotype_reference_images(markers, *, base_dir=None, limit=6)`.
- Produces: `tests/fixtures/image_scoring_baseline.json`, a list of
  `{"marker": {...}, "matches": [{"relative_path": ..., "match_score": ...}]}`
  records that Task 7 must reproduce.

- [ ] **Step 1: Write the baseline capture script**

```python
"""Capture today's image-matching results so the migration can be proved equal.

Run once, against the pre-migration code, with data/phenotype_images present.
"""
import json
from pathlib import Path

from flymanager.utils.phenotypes.image_library import (
    select_phenotype_reference_images,
)
from flymanager.utils.phenotypes.marker_catalog import get_catalog

OUTPUT = Path("tests/fixtures/image_scoring_baseline.json")


def _marker_stubs():
    stubs = []
    # snapshot["definitions"] is a dict keyed by Key, not a list.
    for key, definition in sorted(get_catalog()["definitions"].items()):
        payload = definition.get("payload") or {}
        stubs.append(
            {
                # Payload keys are snake_case; there is no gene_stem key.
                "key": key,
                "phenotype_key": payload.get("phenotype_key"),
                "display_label": payload.get("display_label"),
                "body_part": payload.get("body_part"),
                "effect": payload.get("effect"),
            }
        )
    return stubs


def main():
    records = []
    for stub in _marker_stubs():
        matches = select_phenotype_reference_images([stub], limit=6)
        records.append(
            {
                "marker": stub,
                "matches": [
                    {
                        "relative_path": match["relative_path"],
                        "match_score": match["match_score"],
                    }
                    for match in matches
                ],
            }
        )
    records.sort(key=lambda record: str(record["marker"]["key"]))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(records, indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(records)} records, "
          f"{sum(1 for r in records if r['matches'])} with matches")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and inspect the output**

```bash
python scripts/capture_image_scoring_baseline.py
```

Expected: a few hundred records, a substantial fraction with matches. If
**zero** records have matches, the marker stub field names are wrong — compare
against `_marker_aliases` (`image_library.py:145-164`), which reads
`phenotype_key`, `display_label`, `gene_stem`, `allele_token`,
`balancer_symbol` and `alias_token` off the *marker stub*. Only the first four
have counterparts in `catalog.json` payloads; the stub simply omits the rest.
Fix and re-run before continuing — a baseline of all-empty matches proves
nothing.

- [ ] **Step 3: Write the parity test**

```python
import json
from pathlib import Path

from flymanager.utils.phenotypes.image_library import (
    select_phenotype_reference_images,
)

BASELINE = Path("tests/fixtures/image_scoring_baseline.json")


def test_image_matching_reproduces_the_captured_baseline():
    records = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert records, "baseline fixture is empty"
    assert any(record["matches"] for record in records), \
        "baseline captured no matches at all"

    mismatches = []
    for record in records:
        actual = select_phenotype_reference_images([record["marker"]], limit=6)
        expected_paths = [match["relative_path"] for match in record["matches"]]
        actual_paths = [match["relative_path"] for match in actual]
        if actual_paths != expected_paths:
            mismatches.append((record["marker"]["key"], expected_paths, actual_paths))

    assert not mismatches, f"{len(mismatches)} markers changed: {mismatches[:5]}"
```

`relative_path` is compared, not `match_score`: Task 7 keeps the fuzzy scores
identical, but comparing the selected *images* is the property that actually
matters and it survives the URL change in Task 7 Step 6.

- [ ] **Step 4: Run it against the current code**

Run: `python -m pytest tests/test_marker_image_scoring_parity.py -q`
Expected: PASS. It must pass **now**, before any migration — that is what makes
it a baseline rather than an assertion of the new behavior.

- [ ] **Step 5: Run it under a randomized hash seed three times**

```bash
for i in 1 2 3; do PYTHONHASHSEED=random python -m pytest tests/test_marker_image_scoring_parity.py -q; done
```

Expected: PASS all three. A failure here means today's ordering already depends
on set iteration — report it, because it changes what parity can mean.

- [ ] **Step 6: Commit**

```bash
git add scripts/capture_image_scoring_baseline.py tests/fixtures/image_scoring_baseline.json tests/test_marker_image_scoring_parity.py
git commit -m "test: capture the phenotype image matching baseline before migration"
```

---

### Task 4: The image catalog snapshot

**Files:**
- Create: `flymanager/utils/phenotypes/image_catalog.py`
- Test: `tests/test_marker_image_catalog.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `compile_image_catalog(documents) -> dict` with keys `entries`
  (list), `by_marker_key` (dict of key to entry list), `revision` (int);
  `get_image_catalog() -> dict`; `refresh_image_catalog(db, *, force=False) -> dict`;
  `set_image_catalog_for_testing(snapshot)`; `bump_image_revision(db) -> int`;
  `read_image_revision(db) -> int`. Entries are plain dicts in the Part 2 shape.

Model this file on `flymanager/utils/phenotypes/marker_catalog.py` — read it
first and follow its structure, naming and locking approach rather than
inventing a parallel style.

- [ ] **Step 1: Write the failing test**

```python
from tests.mongo_fakes import FakeDatabase

from flymanager.utils.phenotypes.image_catalog import (
    bump_image_revision,
    compile_image_catalog,
    read_image_revision,
    refresh_image_catalog,
)


def _entry(image_id, marker_keys=(), sort_order=0):
    return {
        "imageId": image_id,
        "storageId": f"s-{image_id}",
        "sha256": image_id * 4,
        "contentType": "image/webp",
        "bytes": 100,
        "width": 64,
        "height": 64,
        "match": {
            "markerKeys": list(marker_keys),
            "aliases": [],
            "stem": image_id,
            "bodyPart": "wing",
            "manifestEntry": False,
            "sourceCollection": "",
        },
        "display": {
            "label": image_id,
            "caption": "",
            "credit": "",
            "provenance": "",
            "priority": 0,
            "sortOrder": sort_order,
        },
        "origin": "user",
    }


def test_compile_indexes_entries_by_marker_key():
    snapshot = compile_image_catalog([
        _entry("a", ["Sb[1]"]),
        _entry("b", ["Sb[1]", "CyO"]),
    ])
    assert len(snapshot["entries"]) == 2
    assert [e["imageId"] for e in snapshot["by_marker_key"]["Sb[1]"]] == ["a", "b"]
    assert [e["imageId"] for e in snapshot["by_marker_key"]["CyO"]] == ["b"]


def test_compile_orders_by_sort_order_then_image_id():
    snapshot = compile_image_catalog([
        _entry("z", ["Sb[1]"], sort_order=0),
        _entry("a", ["Sb[1]"], sort_order=0),
        _entry("m", ["Sb[1]"], sort_order=-1),
    ])
    assert [e["imageId"] for e in snapshot["by_marker_key"]["Sb[1]"]] == ["m", "a", "z"]


def test_compile_skips_entries_with_no_storage_id():
    snapshot = compile_image_catalog([
        _entry("a", ["Sb[1]"]),
        {**_entry("b", ["Sb[1]"]), "storageId": ""},
    ])
    assert [e["imageId"] for e in snapshot["entries"]] == ["a"]


def test_revision_starts_at_zero_and_increments():
    db = FakeDatabase({"settings": [], "marker_images": []})
    assert read_image_revision(db) == 0
    assert bump_image_revision(db) == 1
    assert bump_image_revision(db) == 2
    assert read_image_revision(db) == 2


def test_refresh_recompiles_only_when_the_revision_moves():
    db = FakeDatabase({"settings": [], "marker_images": [_entry("a", ["Sb[1]"])]})
    bump_image_revision(db)
    first = refresh_image_catalog(db, force=True)
    assert [e["imageId"] for e in first["entries"]] == ["a"]

    db.marker_images.insert_one(_entry("b", ["Sb[1]"]))
    unchanged = refresh_image_catalog(db)
    assert [e["imageId"] for e in unchanged["entries"]] == ["a"]

    bump_image_revision(db)
    updated = refresh_image_catalog(db)
    assert [e["imageId"] for e in updated["entries"]] == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_marker_image_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: ... image_catalog`

- [ ] **Step 3: Implement the module**

```python
import threading

from pymongo import ReturnDocument

_LOCK = threading.Lock()
# Serializes read-compile-install so a slow compile of an older revision
# cannot overwrite a newer snapshot that already landed. Production runs
# `gunicorn --workers 1 --threads 12`; marker_catalog.refresh_catalog
# (marker_catalog.py:356-383) guards the same race for the same reason.
_REFRESH_LOCK = threading.Lock()
_SNAPSHOT = {"entries": [], "by_marker_key": {}, "revision": -1}

SETTINGS_FIELD = "markerImageRevision"


def _entry_sort_key(entry):
    display = entry.get("display") or {}
    return (int(display.get("sortOrder") or 0), str(entry.get("imageId") or ""))


def compile_image_catalog(documents, revision=0):
    entries = [
        document
        for document in documents or []
        if document.get("imageId") and document.get("storageId")
    ]
    entries.sort(key=_entry_sort_key)

    by_marker_key = {}
    for entry in entries:
        for key in (entry.get("match") or {}).get("markerKeys") or []:
            by_marker_key.setdefault(str(key), []).append(entry)
    for bucket in by_marker_key.values():
        bucket.sort(key=_entry_sort_key)

    return {
        "entries": entries,
        "by_marker_key": by_marker_key,
        "revision": int(revision or 0),
    }


def get_image_catalog():
    """Return the process-global snapshot. Never queries Mongo."""
    return _SNAPSHOT


def set_image_catalog_for_testing(snapshot):
    global _SNAPSHOT
    with _LOCK:
        _SNAPSHOT = snapshot


def read_image_revision(db):
    document = db["settings"].find_one({}) or {}
    return int(document.get(SETTINGS_FIELD) or 0)


def bump_image_revision(db):
    """Atomically increment markerImageRevision and return the new value.

    find_one_and_update, not update_one: `$inc` is atomic here and the call
    is self-verifying (it returns the incremented document or raises), and
    FakeCollection.update_one applies only `$set` so `$inc` through it is a
    silent no-op. This mirrors bump_marker_catalog_revision at
    utils/mongo/marker_definitions.py:40-63.
    """
    result = db["settings"].find_one_and_update(
        {},
        {"$inc": {SETTINGS_FIELD: 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if not isinstance(result, dict) or SETTINGS_FIELD not in result:
        raise RuntimeError(
            f"Failed to bump {SETTINGS_FIELD}: find_one_and_update "
            f"returned {result!r}"
        )
    return int(result[SETTINGS_FIELD])


def refresh_image_catalog(db, *, force=False):
    """Recompile from Mongo when the stored revision has moved.

    The whole read-compile-install sequence is serialized, for the reason
    given on _REFRESH_LOCK. A compile failure propagates with the previous
    snapshot left installed.
    """
    global _SNAPSHOT

    with _REFRESH_LOCK:
        revision = read_image_revision(db)
        if not force and revision == _SNAPSHOT.get("revision"):
            return _SNAPSHOT
        documents = list(db["marker_images"].find({}))
        snapshot = compile_image_catalog(documents, revision=revision)
        # Assign directly rather than via set_image_catalog_for_testing:
        # threading.Lock is not reentrant and that helper takes _LOCK itself.
        with _LOCK:
            _SNAPSHOT = snapshot
        return snapshot
```

Use `db["settings"]` / `db["marker_images"]` subscript access rather than
attribute access, matching slice A and working identically on `FakeDatabase`
(`mongo_fakes.py:301` auto-creates unseeded collections either way).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_marker_image_catalog.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Assert the no-Mongo invariant explicitly**

The invariant is that `get_image_catalog` reads no database. Assert it by
source inspection rather than by a mock that is never wired to anything:

```python
def test_get_image_catalog_returns_the_installed_snapshot():
    from flymanager.utils.phenotypes import image_catalog

    snapshot = compile_image_catalog([_entry("a", ["Sb[1]"])])
    image_catalog.set_image_catalog_for_testing(snapshot)
    assert image_catalog.get_image_catalog() is snapshot


def test_get_image_catalog_body_contains_no_database_access():
    """The resolution path has no db handle; keep it that way.

    See marker-catalog-invariants: get_catalog() must never query Mongo, and
    the same rule binds the image catalog because the scorer runs on that
    same db-free path.
    """
    import inspect

    from flymanager.utils.phenotypes import image_catalog

    source = inspect.getsource(image_catalog.get_image_catalog)
    for forbidden in ("db", "find(", "find_one", "settings"):
        assert forbidden not in source, (
            f"get_image_catalog references {forbidden!r}"
        )
```

Run: `python -m pytest tests/test_marker_image_catalog.py -q`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/phenotypes/image_catalog.py tests/test_marker_image_catalog.py
git commit -m "feat: compile marker image entries into a process-global snapshot"
```

---

### Task 5: The seed builder

**Files:**
- Create: `scripts/build_marker_image_seed.py`
- Creates output: `data/markers/images/` (254 `.webp` + `index.json`)
- Test: `tests/test_marker_image_seed_index.py`

**Interfaces:**
- Consumes: `normalize_image` (Task 2).
- Produces: `data/markers/images/index.json` — a list of entry documents in the
  Part 2 shape minus `storageId`, each with an added `"file"` field naming its
  `.webp` sibling.

- [ ] **Step 1: Write the builder**

**The builder must not import from `image_library.py`.** Task 7 deletes
`_image_entries` and `resolve_phenotype_image_library_path`, and Task 8's
rollback ("rebuild the seed") has to still work after that. So the script
**vendors its own frozen copies** of the manifest and scan readers. Copy them
verbatim from `image_library.py:56-143` as it stands right now, before Task 7
touches it. They are a snapshot of a thing being deleted; duplication is the
point.

**Entry order matters.** `_image_entries` returns manifest entries first, then
sorted scan entries, and the selector keeps the *first* entry on a score tie
(`if score > best_score`). If the seed sets every `sortOrder` to 0, the catalog
orders by `imageId` — a content hash, effectively random — and ties resolve to
a different image than they do today. So `sortOrder` records the original
index.

```python
"""One-time developer tool: build the committed marker image seed.

Reads data/phenotype_images/ (manifest + scan) and writes data/markers/images/
containing normalized WebP files plus index.json. Deterministic: running it
twice produces byte-identical output.

The manifest/scan readers below are FROZEN COPIES of image_library.py's, taken
before that module was rewritten to read from the catalog. Do not re-import
them: this script has to keep working after those functions are deleted, so
that the seed can be rebuilt if the migration has to be rolled back.
"""
import json
import shutil
from pathlib import Path

from flymanager.utils.phenotypes.image_normalize import normalize_image

OUTPUT_DIR = Path("data/markers/images")
SOURCE_DIR = Path("data/phenotype_images")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# --- frozen copies: _normalize_key, _manifest_entries, _scanned_entries,
# --- _image_entries, copied verbatim from image_library.py:48-143.
# --- (paste them here unchanged)


def main():
    source = SOURCE_DIR
    # Preserve _image_entries' order exactly: manifest entries first, then
    # sorted scan entries. Do NOT re-sort — the order breaks score ties.
    entries = _image_entries(source)

    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    index = []
    for position, entry in enumerate(entries):
        raw = (source / entry["relative_path"]).read_bytes()
        normalized = normalize_image(raw, max_bytes=None)
        image_id = f"img_{normalized.sha256[:16]}"
        filename = f"{image_id}.webp"
        (OUTPUT_DIR / filename).write_bytes(normalized.data)
        index.append(
            {
                "imageId": image_id,
                "file": filename,
                "sha256": normalized.sha256,
                "contentType": normalized.content_type,
                "bytes": normalized.bytes,
                "width": normalized.width,
                "height": normalized.height,
                "match": {
                    "markerKeys": [],
                    "aliases": sorted(entry["aliases"]),
                    "stem": entry["normalized_stem"],
                    "bodyPart": entry["body_part"],
                    "manifestEntry": bool(entry["manifest_entry"]),
                    "sourceCollection": entry["source_collection"],
                },
                "display": {
                    "label": entry.get("stem", ""),
                    "caption": entry.get("notes", ""),
                    "credit": entry.get("credit", ""),
                    "provenance": entry.get("provenance", ""),
                    "sourceName": entry.get("source_name", ""),
                    "sourceUrl": entry.get("source_url", ""),
                    # Kept so the Task 7 parity test can map a returned
                    # imageId back to the path the old scorer selected.
                    "sourcePath": entry["relative_path"],
                    "priority": int(entry.get("priority", 0) or 0),
                    # Preserves _image_entries' order so score ties resolve
                    # to the same image they do today.
                    "sortOrder": position,
                },
                "origin": "shipped",
            }
        )

    # Byte-identical files collapse to one imageId. Union the aliases, but
    # REFUSE to merge when stem or bodyPart differ: the old scorer saw two
    # distinct match surfaces and silently keeping one changes results.
    merged = {}
    conflicts = []
    for record in index:
        existing = merged.get(record["imageId"])
        if existing is None:
            merged[record["imageId"]] = record
            continue
        for field in ("stem", "bodyPart"):
            if existing["match"][field] != record["match"][field]:
                conflicts.append(
                    (record["imageId"], field,
                     existing["match"][field], record["match"][field])
                )
        existing["match"]["aliases"] = sorted(
            set(existing["match"]["aliases"]) | set(record["match"]["aliases"])
        )

    if conflicts:
        raise SystemExit(
            "Byte-identical images disagree on stem/bodyPart; merging them "
            f"would change matching. Resolve before seeding: {conflicts}"
        )

    output = sorted(merged.values(), key=lambda r: r["display"]["sortOrder"])
    (OUTPUT_DIR / "index.json").write_text(
        json.dumps(output, indent=1, sort_keys=True), encoding="utf-8"
    )
    total = sum(r["bytes"] for r in output)
    print(f"{len(output)} entries, {total / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
python scripts/build_marker_image_seed.py
```

Expected: roughly 254 entries (fewer if any shipped files are byte-identical
after normalization) and a total in the 4-7 MB range. Report the exact numbers.

- [ ] **Step 3: Verify determinism**

```bash
python scripts/build_marker_image_seed.py
md5 data/markers/images/index.json
python scripts/build_marker_image_seed.py
md5 data/markers/images/index.json
```

Expected: identical hashes. If not, something iterates a set — find it and sort
it, per the global constraints.

- [ ] **Step 4: Write the seed integrity test**

```python
import hashlib
import json
from pathlib import Path

SEED_DIR = Path("data/markers/images")


def _index():
    return json.loads((SEED_DIR / "index.json").read_text(encoding="utf-8"))


def test_seed_index_is_present_and_substantial():
    assert len(_index()) > 200


def test_every_seed_entry_has_a_file_matching_its_hash():
    for record in _index():
        path = SEED_DIR / record["file"]
        assert path.is_file(), record["file"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert len(data) == record["bytes"]


def test_seed_image_ids_are_unique():
    ids = [record["imageId"] for record in _index()]
    assert len(ids) == len(set(ids))


def test_seed_stays_small_enough_to_ship_in_git():
    total = sum(record["bytes"] for record in _index())
    assert total < 10_000_000, f"seed grew to {total / 1e6:.1f} MB"


def test_no_seed_file_is_orphaned():
    referenced = {record["file"] for record in _index()}
    on_disk = {path.name for path in SEED_DIR.glob("*.webp")}
    assert on_disk == referenced
```

- [ ] **Step 5: Run it**

Run: `python -m pytest tests/test_marker_image_seed_index.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add scripts/build_marker_image_seed.py data/markers/images tests/test_marker_image_seed_index.py
git commit -m "feat: build the committed marker image seed from the shipped library"
```

---

### Task 6: The seed loader

**Files:**
- Create: `flymanager/utils/phenotypes/image_seed.py`
- Test: `tests/test_marker_image_seed_loader.py`

**Interfaces:**
- Consumes: `ImageStore` (Task 1), `bump_image_revision` (Task 4), the seed
  directory (Task 5).
- Produces: `load_image_seed(db, store, *, seed_dir=None) -> int` returning the
  number of entries inserted; `upsert_image_entry(db, store, normalized, *, marker_key, uploaded_by, display=None) -> dict`
  returning the stored entry document.

- [ ] **Step 1: Write the failing test**

```python
import json

from tests.mongo_fakes import FakeDatabase

from flymanager.utils.phenotypes.image_seed import load_image_seed
from flymanager.utils.phenotypes.image_store import MemoryImageStore


def _write_seed(tmp_path, count=2):
    records = []
    for index in range(count):
        data = f"fake-webp-{index}".encode()
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        filename = f"img_{digest[:16]}.webp"
        (tmp_path / filename).write_bytes(data)
        records.append(
            {
                "imageId": f"img_{digest[:16]}",
                "file": filename,
                "sha256": digest,
                "contentType": "image/webp",
                "bytes": len(data),
                "width": 10,
                "height": 10,
                "match": {
                    "markerKeys": [],
                    "aliases": ["a"],
                    "stem": "a",
                    "bodyPart": "wing",
                    "manifestEntry": True,
                    "sourceCollection": "library",
                },
                "display": {"label": "a", "priority": 1, "sortOrder": 0},
                "origin": "shipped",
            }
        )
    (tmp_path / "index.json").write_text(json.dumps(records), encoding="utf-8")
    return tmp_path


def test_load_inserts_every_seed_entry(tmp_path):
    seed = _write_seed(tmp_path)
    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()

    assert load_image_seed(db, store, seed_dir=seed) == 2
    stored = list(db.marker_images.find({}))
    assert len(stored) == 2
    assert all(store.exists(entry["storageId"]) for entry in stored)


def test_load_is_idempotent(tmp_path):
    seed = _write_seed(tmp_path)
    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()

    load_image_seed(db, store, seed_dir=seed)
    assert load_image_seed(db, store, seed_dir=seed) == 0
    assert len(list(db.marker_images.find({}))) == 2


def test_load_fills_in_only_missing_entries(tmp_path):
    seed = _write_seed(tmp_path, count=3)
    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()

    records = json.loads((seed / "index.json").read_text(encoding="utf-8"))
    db.marker_images.insert_one({**records[0], "storageId": "pre-existing"})

    assert load_image_seed(db, store, seed_dir=seed) == 2
    assert len(list(db.marker_images.find({}))) == 3


def test_load_bumps_the_revision_only_when_it_inserts(tmp_path):
    from flymanager.utils.phenotypes.image_catalog import read_image_revision

    seed = _write_seed(tmp_path)
    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()

    load_image_seed(db, store, seed_dir=seed)
    after_first = read_image_revision(db)
    assert after_first > 0

    load_image_seed(db, store, seed_dir=seed)
    assert read_image_revision(db) == after_first


def test_missing_seed_directory_raises(tmp_path):
    import pytest

    db = FakeDatabase({"settings": [], "marker_images": []})
    with pytest.raises(FileNotFoundError):
        load_image_seed(db, MemoryImageStore(), seed_dir=tmp_path / "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_marker_image_seed_loader.py -q`
Expected: FAIL — `ModuleNotFoundError: ... image_seed`

- [ ] **Step 3: Implement the loader**

```python
import json
import logging
from pathlib import Path

from flymanager.utils.phenotypes.image_catalog import bump_image_revision

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SEED_DIR = REPO_ROOT / "data" / "markers" / "images"


def resolve_seed_dir(seed_dir=None):
    return Path(seed_dir) if seed_dir is not None else DEFAULT_SEED_DIR


def load_image_seed(db, store, *, seed_dir=None):
    """Insert any seed entry not already present, keyed by sha256.

    Idempotent: safe to run on every startup, on a fresh or existing deploy.
    """
    base = resolve_seed_dir(seed_dir)
    index_path = base / "index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"marker image seed index missing: {index_path}")

    records = json.loads(index_path.read_text(encoding="utf-8"))
    existing = {
        document.get("sha256")
        for document in db.marker_images.find({}, {"sha256": 1})
    }

    inserted = 0
    for record in records:
        if record.get("sha256") in existing:
            continue
        data = (base / record["file"]).read_bytes()
        storage_id = store.put(data, content_type=record["contentType"])
        document = {
            key: value for key, value in record.items() if key != "file"
        }
        document["storageId"] = storage_id
        document["UploadedBy"] = "system"
        try:
            db.marker_images.insert_one(document)
        except Exception:
            store.delete(storage_id)
            raise
        inserted += 1

    if inserted:
        bump_image_revision(db)
        logger.info("loaded %s marker image seed entries", inserted)
    return inserted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_marker_image_seed_loader.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Add the upsert used by uploads**

Append to `image_seed.py`:

```python
def upsert_image_entry(db, store, normalized, *, marker_key,
                       uploaded_by, display=None):
    """Store a normalized image and bind it to a marker.

    Identity is the content hash, so re-uploading the same bytes for another
    marker appends to markerKeys rather than duplicating the entry.
    """
    image_id = f"img_{normalized.sha256[:16]}"
    existing = db.marker_images.find_one({"imageId": image_id})
    if existing is not None:
        keys = list((existing.get("match") or {}).get("markerKeys") or [])
        if marker_key not in keys:
            keys.append(marker_key)
            db.marker_images.update_one(
                {"imageId": image_id},
                {"$set": {"match.markerKeys": keys}},
            )
            bump_image_revision(db)
        return db.marker_images.find_one({"imageId": image_id})

    storage_id = store.put(normalized.data, content_type=normalized.content_type)
    document = {
        "imageId": image_id,
        "storageId": storage_id,
        "sha256": normalized.sha256,
        "contentType": normalized.content_type,
        "bytes": normalized.bytes,
        "width": normalized.width,
        "height": normalized.height,
        "match": {
            "markerKeys": [marker_key],
            "aliases": [],
            "stem": "",
            "bodyPart": "",
            "manifestEntry": False,
            "sourceCollection": "",
        },
        "display": {
            "label": (display or {}).get("label", ""),
            "caption": (display or {}).get("caption", ""),
            "credit": (display or {}).get("credit", ""),
            "provenance": "User upload",
            "priority": 3,
            "sortOrder": (display or {}).get("sortOrder", 0),
        },
        "origin": "user",
        "UploadedBy": uploaded_by,
    }
    try:
        db.marker_images.insert_one(document)
    except Exception:
        store.delete(storage_id)
        raise
    bump_image_revision(db)
    return document
```

Add tests for it in the same file:

```python
def test_upsert_creates_a_user_entry():
    from flymanager.utils.phenotypes.image_normalize import normalize_image
    from flymanager.utils.phenotypes.image_seed import upsert_image_entry
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, format="PNG")
    normalized = normalize_image(buffer.getvalue())

    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()
    entry = upsert_image_entry(
        db, store, normalized, marker_key="Sb[1]", uploaded_by="alice"
    )
    assert entry["match"]["markerKeys"] == ["Sb[1]"]
    assert entry["origin"] == "user"
    assert store.exists(entry["storageId"])


def test_upsert_appends_a_marker_key_for_identical_bytes():
    from flymanager.utils.phenotypes.image_normalize import normalize_image
    from flymanager.utils.phenotypes.image_seed import upsert_image_entry
    import io
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buffer, format="PNG")
    normalized = normalize_image(buffer.getvalue())

    db = FakeDatabase({"settings": [], "marker_images": []})
    store = MemoryImageStore()
    upsert_image_entry(db, store, normalized, marker_key="Sb[1]", uploaded_by="alice")
    upsert_image_entry(db, store, normalized, marker_key="CyO", uploaded_by="bob")

    stored = list(db.marker_images.find({}))
    assert len(stored) == 1
    assert stored[0]["match"]["markerKeys"] == ["Sb[1]", "CyO"]
```

Run: `python -m pytest tests/test_marker_image_seed_loader.py -q`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/phenotypes/image_seed.py tests/test_marker_image_seed_loader.py
git commit -m "feat: load the marker image seed idempotently and upsert uploads"
```

---

### Task 7: Rewire the scorer onto the catalog

This is the task the Task 3 baseline exists to protect. The scoring algorithm
is **ported verbatim**; only its input changes from filesystem dicts to catalog
entries.

**Files:**
- Modify: `flymanager/utils/phenotypes/image_library.py`
- Test: `tests/test_marker_image_matching.py`, plus the existing
  `tests/test_marker_image_scoring_parity.py`

**Interfaces:**
- Consumes: `get_image_catalog()` (Task 4).
- Produces: `select_phenotype_reference_images(markers, *, limit=6)` and
  `select_prediction_reference_images(prediction, *, limit=6)` — the `base_dir`
  parameter is **removed**. Each returned match dict keeps its existing keys
  except `relative_path`, which is replaced by `image_id` and `image_url`.
  New: `EXACT_KEY_SCORE = 1000`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_catalog import compile_image_catalog
from flymanager.utils.phenotypes.image_library import (
    select_phenotype_reference_images,
)


def _entry(image_id, *, marker_keys=(), aliases=(), stem="", body_part="wing",
           manifest=False, priority=0):
    return {
        "imageId": image_id,
        "storageId": f"s-{image_id}",
        "sha256": "0" * 64,
        "contentType": "image/webp",
        "bytes": 1, "width": 8, "height": 8,
        "match": {
            "markerKeys": list(marker_keys),
            "aliases": list(aliases),
            "stem": stem,
            "bodyPart": body_part,
            "manifestEntry": manifest,
            "sourceCollection": "",
        },
        "display": {"label": image_id, "caption": "", "credit": "",
                    "provenance": "", "priority": priority, "sortOrder": 0},
        "origin": "user",
    }


def _install(entries):
    image_catalog.set_image_catalog_for_testing(compile_image_catalog(entries))


@pytest.fixture(autouse=True)
def _reset_catalog():
    """The snapshot is process-global, so tests leak into each other.

    Without this, whichever module runs last wins and the parity test can
    score against a three-entry fixture catalog.
    """
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1}
    )


def test_exact_marker_key_beats_a_stem_exact_fuzzy_match():
    _install([
        _entry("img_upload", marker_keys=["Sb[1]"]),
        _entry("img_fuzzy", stem="sb", manifest=True, priority=3),
    ])
    matches = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"},
    ])
    assert [m["image_id"] for m in matches] == ["img_upload"]


def test_falls_back_to_fuzzy_matching_when_no_upload_exists():
    _install([_entry("img_fuzzy", stem="sb", body_part="bristle")])
    matches = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "bristle"},
    ])
    assert [m["image_id"] for m in matches] == ["img_fuzzy"]


def test_body_part_gate_still_excludes_mismatched_entries():
    _install([_entry("img_fuzzy", stem="sb", body_part="eye")])
    matches = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"},
    ])
    assert matches == []


def test_matches_carry_a_servable_url():
    _install([_entry("img_upload", marker_keys=["Sb[1]"])])
    match = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"},
    ])[0]
    assert match["image_url"].endswith("/markers/images/img_upload")


def test_the_same_image_is_not_returned_twice():
    _install([_entry("img_one", marker_keys=["Sb[1]", "CyO"])])
    matches = select_phenotype_reference_images([
        {"key": "Sb[1]", "display_label": "Sb", "body_part": "wing"},
        {"key": "CyO", "display_label": "CyO", "body_part": "wing"},
    ])
    assert [m["image_id"] for m in matches] == ["img_one"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_marker_image_matching.py -q`
Expected: FAIL — `KeyError: 'image_id'` or a `TypeError` on the entry shape.

- [ ] **Step 3: Rewrite `image_library.py`**

Delete `resolve_phenotype_image_library_path`, `_manifest_entries`,
`_scanned_entries`, `_image_entries`, `IMAGE_SUFFIXES`,
`DEFAULT_IMAGE_LIBRARY_PATH`, `DEFAULT_MANIFEST_FILENAME` and the `json`/`os`/
`Path` imports. **Keep** `_normalize_key`, `_marker_aliases`,
`BODY_PART_ALIASES`, `EPISTASIS_IMAGE_ALIASES`, `_phenotype_image_aliases`,
`_labels_to_marker_stubs`, `_split_summary_labels` and
`_select_reference_markers_from_prediction` exactly as they are.

Adapt the two entry-reading helpers to the catalog shape, preserving every
constant in the scoring arithmetic:

```python
EXACT_KEY_SCORE = 1000


def _entry_field(entry, name, default=""):
    return (entry.get("match") or {}).get(name, default)


def _body_part_matches(marker, entry):
    marker_body_part = str(marker.get("body_part") or "").strip().lower()
    entry_body_part = str(_entry_field(entry, "bodyPart")).strip().lower()
    if not marker_body_part or not entry_body_part:
        return False
    aliases = BODY_PART_ALIASES.get(marker_body_part)
    if aliases:
        return entry_body_part in aliases
    aliases = BODY_PART_ALIASES.get(entry_body_part)
    if aliases:
        return marker_body_part in aliases
    return marker_body_part == entry_body_part


def _score_entry(marker, aliases, entry):
    marker_key = str(marker.get("key") or "")
    if marker_key and marker_key in (_entry_field(entry, "markerKeys", []) or []):
        return EXACT_KEY_SCORE

    entry_stem = str(_entry_field(entry, "stem"))
    if not entry_stem:
        return 0
    marker_body_part = str(marker.get("body_part") or "").strip().lower()
    entry_body_part = str(_entry_field(entry, "bodyPart")).strip().lower()
    if marker_body_part and entry_body_part and not _body_part_matches(marker, entry):
        return 0

    entry_aliases = set(_entry_field(entry, "aliases", []) or [])
    manifest_entry = bool(_entry_field(entry, "manifestEntry", False))
    allow_stem_fallback = not (manifest_entry and entry_aliases)
    score = 0
    for alias in aliases:
        if alias and alias in entry_aliases:
            score = max(score, 98)
        elif allow_stem_fallback and entry_stem == alias:
            score = max(score, 100)
        elif allow_stem_fallback and alias and entry_stem.startswith(alias):
            score = max(score, 88)
        elif allow_stem_fallback and alias and alias in entry_stem:
            score = max(score, 72)
    if score <= 0:
        return 0
    if score and _body_part_matches(marker, entry):
        score += 8
    if _entry_field(entry, "sourceCollection") == "learning_to_fly":
        score += 4
    if manifest_entry:
        score += 6
    score += int((entry.get("display") or {}).get("priority", 0) or 0)
    return score
```

Then the selector, keeping its dedup and limit behavior:

```python
def select_phenotype_reference_images(markers, *, limit=6):
    entries = get_image_catalog()["entries"]
    if not entries:
        return []

    matches = []
    seen_images = set()
    seen_markers = set()

    for marker in markers or []:
        marker_key = (
            marker.get("phenotype_key"),
            marker.get("display_label"),
            marker.get("allele_token"),
        )
        if marker_key in seen_markers:
            continue
        seen_markers.add(marker_key)

        aliases = _marker_aliases(marker)
        if not aliases and not marker.get("key"):
            continue

        best_entry = None
        best_score = 0
        for entry in entries:
            score = _score_entry(marker, aliases, entry)
            # Strictly greater keeps the first entry on a tie, and `entries`
            # is already in total order from compile_image_catalog.
            if score > best_score:
                best_entry = entry
                best_score = score
        if best_entry is None or best_score <= 0:
            continue

        image_id = best_entry["imageId"]
        if image_id in seen_images:
            continue
        seen_images.add(image_id)

        display = best_entry.get("display") or {}
        matches.append(
            {
                "image_id": image_id,
                "image_url": f"/markers/images/{image_id}",
                "display_label": marker.get("display_label", marker.get("gene_stem", "?")),
                "body_part": marker.get("body_part", ""),
                "effect": marker.get("effect", ""),
                "source_collection": _entry_field(best_entry, "sourceCollection"),
                "source_name": display.get("sourceName", ""),
                "provenance": display.get("provenance", ""),
                "credit": display.get("credit", ""),
                "source_url": display.get("sourceUrl", ""),
                "notes": display.get("caption", ""),
                "match_score": best_score,
            }
        )
        if len(matches) >= limit:
            break

    return matches


def select_prediction_reference_images(prediction, *, limit=6):
    return select_phenotype_reference_images(
        _select_reference_markers_from_prediction(prediction or {}),
        limit=limit,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_marker_image_matching.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Point the parity test at the migrated path**

Two things must change, and the first is easy to miss: the rewritten selector
reads **only** `get_image_catalog()`, which is empty in a unit test. The parity
test must build a snapshot from the seed index itself, or it will compare
"no matches" against "no matches" and pass while proving nothing.

The bridge from `relative_path` to `image_id` needs a mapping the seed does not
currently record. Add `"sourcePath": entry["relative_path"]` to each record in
the Task 5 builder's `index` (inside `display`), rebuild the seed, then:

```python
import json
from pathlib import Path

import pytest

from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_catalog import compile_image_catalog
from flymanager.utils.phenotypes.image_library import (
    select_phenotype_reference_images,
)

BASELINE = Path("tests/fixtures/image_scoring_baseline.json")
SEED_DIR = Path("data/markers/images")


@pytest.fixture(autouse=True)
def _seed_catalog():
    """Install the shipped seed as the catalog for this module.

    The selector is db-free and reads only the process-global snapshot, so
    without this the parity test scores against an empty catalog.
    """
    records = json.loads((SEED_DIR / "index.json").read_text(encoding="utf-8"))
    entries = [
        {**record, "storageId": f"seed-{record['imageId']}"}
        for record in records
    ]
    image_catalog.set_image_catalog_for_testing(compile_image_catalog(entries))
    yield


def test_image_matching_reproduces_the_captured_baseline():
    records = json.loads(BASELINE.read_text(encoding="utf-8"))
    seed = json.loads((SEED_DIR / "index.json").read_text(encoding="utf-8"))
    path_for_image = {
        record["imageId"]: record["display"]["sourcePath"] for record in seed
    }

    assert any(record["matches"] for record in records), \
        "baseline captured no matches at all"

    mismatches = []
    for record in records:
        actual = select_phenotype_reference_images([record["marker"]], limit=6)
        expected = [match["relative_path"] for match in record["matches"]]
        got = [path_for_image.get(match["image_id"]) for match in actual]
        if got != expected:
            mismatches.append((record["marker"]["key"], expected, got))

    assert not mismatches, (
        f"{len(mismatches)} markers changed: {mismatches[:5]}"
    )
```

The fixture captured in Task 3 is **not** regenerated. Regenerating it through
the new code path would make the test assert that the new behavior equals
itself, which is worthless — the whole point is that it still holds the *old*
answers.

Run: `PYTHONHASHSEED=random python -m pytest tests/test_marker_image_scoring_parity.py -q`
Expected: PASS. **If it fails, stop and report the mismatching markers** — a
regression here is exactly the failure mode this slice was designed to avoid.
Do not "fix" it by rewriting the fixture.

- [ ] **Step 6: Update the templates and route callers**

**All five call sites DO pass `base_dir`** and will `TypeError` until fixed —
`cross.py:97,112,143` and `stock.py:62,71`, each as
`base_dir=current_app.config.get("PHENOTYPE_IMAGE_LIBRARY_PATH")`. Delete that
argument at every one, and drop the now-unused `current_app` import if nothing
else in the file uses it.

```bash
grep -n "base_dir" flymanager/app/routes/cross.py flymanager/app/routes/stock.py
```

Expected after editing: no matches.

Then the templates that render matches:

```bash
grep -rn "relative_path\|phenotype_reference_image" flymanager/app/templates/
```

Replace `url_for('stock.phenotype_reference_image', relative_path=...)` with
`match.image_url`.

- [ ] **Step 7: Run the full suite**

Run the full test command from Global Constraints.
Expected: no more than the 15 baseline failures. Tests in
`tests/test_phenotype_routes.py` that reference
`/stock/phenotype_image/...`, `_write_test_phenotype_image_library` or
`base_dir=` will fail — update them to install a catalog snapshot via
`set_image_catalog_for_testing` instead. Lines to expect work at:
`test_phenotype_routes.py:68, 279, 372-373, 391-535, 888, 1264, 1289, 1316`.

- [ ] **Step 8: Commit**

```bash
git add flymanager/utils/phenotypes/image_library.py tests/
git commit -m "refactor: score phenotype images from the catalog instead of the filesystem"
```

---

### Task 8: Routes, wiring, and retiring the filesystem library

The only irreversible step. Everything before it passes with the old library
still on disk.

**Files:**
- Modify: `flymanager/app/routes/markers.py`
- Modify: `flymanager/app/routes/stock.py` (remove `phenotype_reference_image`)
- Modify: `flymanager/app/__init__.py`
- Delete: `data/phenotype_images/`
- Test: `tests/test_marker_image_routes.py`

**Interfaces:**
- Consumes: `normalize_image`, `ImageRejected` (Task 2); `upsert_image_entry`,
  `load_image_seed` (Task 6); `refresh_image_catalog`, `bump_image_revision`
  (Task 4); `GridFSImageStore` (Task 1).
- Produces: routes `POST /markers/<key>/images`,
  `POST /markers/images/<image_id>/delete`, `GET /markers/images/<image_id>`.

- [ ] **Step 1: Build the test scaffolding — none exists**

There is no `tests/conftest.py` and no `client` / `logged_in_client` /
`seeded_image` fixture anywhere in the suite. `test_phenotype_routes.py` builds
clients inline and CSRF is enforced globally, so every POST needs a token.
Write these module-local helpers at the top of
`tests/test_marker_image_routes.py`, following that file's existing pattern —
read `test_phenotype_routes.py:265-280` first and copy how it obtains a token.

```python
import io
import re
from unittest.mock import patch

import pytest
from PIL import Image

from flymanager.app import create_app, db
from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_catalog import compile_image_catalog


def _png(color=(1, 2, 3)):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _extract_csrf_token(response_text):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def _login(test_client, username="testuser"):
    with test_client.session_transaction() as session:
        session["username"] = username
        session["logged_in"] = True


@pytest.fixture
def logged_in_client(client):
    _login(client)
    return client


@pytest.fixture
def admin_client(client):
    _login(client, username="admin")
    return client


@pytest.fixture
def csrf(logged_in_client):
    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        response = logged_in_client.get("/flip/")
    return _extract_csrf_token(response.get_data(as_text=True))


@pytest.fixture
def seeded_image():
    """Insert one shipped-origin image entry plus its bytes, then clean up."""
    from flymanager.utils.phenotypes.image_normalize import normalize_image
    from flymanager.utils.phenotypes.image_store import GridFSImageStore

    normalized = normalize_image(_png((7, 7, 7)))
    store = GridFSImageStore(db)
    storage_id = store.put(normalized.data, content_type=normalized.content_type)
    document = {
        "imageId": f"img_{normalized.sha256[:16]}",
        "storageId": storage_id,
        "sha256": normalized.sha256,
        "contentType": "image/webp",
        "bytes": normalized.bytes,
        "width": normalized.width,
        "height": normalized.height,
        "match": {"markerKeys": ["Sb[1]"], "aliases": [], "stem": "sb",
                  "bodyPart": "bristle", "manifestEntry": True,
                  "sourceCollection": "library"},
        "display": {"label": "Sb", "caption": "", "credit": "Test credit",
                    "provenance": "", "priority": 1, "sortOrder": 0},
        "origin": "shipped",
        "UploadedBy": "system",
    }
    db["marker_images"].delete_many({"imageId": document["imageId"]})
    db["marker_images"].insert_one(document)
    image_catalog.refresh_image_catalog(db, force=True)
    yield document
    db["marker_images"].delete_many({"imageId": document["imageId"]})
    store.delete(storage_id)


@pytest.fixture(autouse=True)
def _reset_image_catalog():
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1}
    )
```

Check `create_app` is the real factory name and that the session keys match
what `login_required` reads (`flymanager/app/routes/auth.py`) before relying on
`_login`.

- [ ] **Step 2: Write the failing route tests**

Note every POST passes a CSRF token, and every URL starts with `/markers` —
the blueprint has no url_prefix.

```python
def test_upload_requires_login(client):
    response = client.post("/markers/Sb%5B1%5D/images")
    assert response.status_code in (302, 401)


def test_upload_stores_a_normalized_entry(logged_in_client, csrf):
    response = logged_in_client.post(
        "/markers/Sb%5B1%5D/images",
        data={"image": (io.BytesIO(_png()), "photo.png"), "csrf_token": csrf},
        content_type="multipart/form-data",
    )
    assert response.status_code in (200, 302)
    assert db["marker_images"].find_one({"match.markerKeys": "Sb[1]"}) is not None


def test_upload_binds_to_the_exact_marker_key(logged_in_client, csrf):
    """Guards against a `/<path:key>/images` rule swallowing the prefix.

    `path:` converters span slashes, so a rule missing its /markers prefix
    still matches this URL — with key="markers/Sb[1]".
    """
    logged_in_client.post(
        "/markers/Sb%5B1%5D/images",
        data={"image": (io.BytesIO(_png((4, 5, 6))), "p.png"), "csrf_token": csrf},
        content_type="multipart/form-data",
    )
    keys = [
        key
        for document in db["marker_images"].find({})
        for key in (document.get("match") or {}).get("markerKeys") or []
    ]
    assert "markers/Sb[1]" not in keys


def test_upload_rejects_an_unknown_marker(logged_in_client, csrf):
    response = logged_in_client.post(
        "/markers/NotAMarker/images",
        data={"image": (io.BytesIO(_png()), "p.png"), "csrf_token": csrf},
        content_type="multipart/form-data",
    )
    assert response.status_code == 404


def test_upload_rejects_a_non_image(logged_in_client, csrf):
    response = logged_in_client.post(
        "/markers/Sb%5B1%5D/images",
        data={"image": (io.BytesIO(b"not an image"), "evil.png"),
              "csrf_token": csrf},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"not a readable image" in response.data.lower()


def test_serving_route_returns_bytes_and_an_etag(logged_in_client, seeded_image):
    response = logged_in_client.get(f"/markers/images/{seeded_image['imageId']}")
    assert response.status_code == 200
    assert response.headers["ETag"].strip('"') == seeded_image["sha256"]
    assert "immutable" in response.headers["Cache-Control"]


def test_serving_route_honours_if_none_match(logged_in_client, seeded_image):
    response = logged_in_client.get(
        f"/markers/images/{seeded_image['imageId']}",
        headers={"If-None-Match": f'"{seeded_image["sha256"]}"'},
    )
    assert response.status_code == 304


def test_serving_route_requires_login(client, seeded_image):
    response = client.get(f"/markers/images/{seeded_image['imageId']}")
    assert response.status_code in (302, 401)


def test_serving_route_404s_for_an_unknown_id(logged_in_client):
    assert logged_in_client.get("/markers/images/img_nope").status_code == 404


def test_deleting_a_shipped_image_is_refused_for_non_admins(
    logged_in_client, csrf, seeded_image
):
    response = logged_in_client.post(
        f"/markers/images/{seeded_image['imageId']}/delete",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 403
    assert db["marker_images"].find_one({"imageId": seeded_image["imageId"]})
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_marker_image_routes.py -q`
Expected: FAIL — 404s, because the routes do not exist.

- [ ] **Step 4: Add the routes**

> Every rule spells out `/markers`: the blueprint is registered without a
> url_prefix (`__init__.py:299`), matching slice A's `@bp.get("/markers/<path:key>")`.

```python
@bp.post("/markers/<path:key>/images")
@login_required
@limiter.limit("20 per minute")
def upload_marker_image(key):
    # Bind only to a marker that exists, so a typo cannot mint an orphan
    # entry that no detail page will ever render.
    if key not in get_catalog()["definitions"]:
        abort(404)

    uploaded = request.files.get("image")
    if uploaded is None:
        flash("No image was submitted.", "error")
        return redirect(url_for("markers.marker_detail", key=key))

    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        flash("Images must be 2 MB or smaller.", "error")
        return redirect(url_for("markers.marker_detail", key=key))

    try:
        normalized = normalize_image(raw)
    except ImageRejected as exc:
        flash(str(exc), "error")
        return redirect(url_for("markers.marker_detail", key=key))

    entry = upsert_image_entry(
        db,
        GridFSImageStore(db),
        normalized,
        marker_key=key,
        uploaded_by=session.get("username", ""),
        display={"caption": request.form.get("caption", "").strip()},
    )
    refresh_image_catalog(db, force=True)
    # write_activity(user, activity, db) -- three args, db LAST.
    write_activity(
        session.get("username", ""),
        f"Uploaded image {entry['imageId']} for marker {key}",
        db,
    )
    flash("Image uploaded.", "success")
    return redirect(url_for("markers.marker_detail", key=key))


@bp.get("/markers/images/<image_id>")
@login_required
@limiter.limit("60 per minute")
def serve_marker_image(image_id):
    entry = db["marker_images"].find_one({"imageId": image_id})
    if entry is None:
        abort(404)
    try:
        stream = GridFSImageStore(db).open(entry["storageId"])
    except ImageNotFound:
        current_app.logger.warning(
            "marker image %s has no bytes behind storageId %s",
            image_id, entry.get("storageId"),
        )
        abort(404)

    response = send_file(
        stream,
        mimetype=entry.get("contentType", "image/webp"),
        # conditional=True is what turns the ETag into a 304 instead of
        # re-sending the bytes to every client that revalidates.
        conditional=True,
    )
    response.set_etag(entry["sha256"])
    # imageId derives from content, so a given id's bytes never change.
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response.make_conditional(request)


@bp.post("/markers/images/<image_id>/delete")
@login_required
def delete_marker_image(image_id):
    entry = db["marker_images"].find_one({"imageId": image_id})
    if entry is None:
        abort(404)

    username = session.get("username", "")
    is_admin = username == "admin"
    if entry.get("origin") == "shipped" and not is_admin:
        abort(403)
    if not is_admin and entry.get("UploadedBy") != username:
        abort(403)

    # An entry bound to several markers (identical bytes uploaded twice) is
    # unbound from this one rather than destroyed for everyone.
    marker_keys = list((entry.get("match") or {}).get("markerKeys") or [])
    unbind_key = request.form.get("marker_key", "").strip()
    if unbind_key and len(marker_keys) > 1 and unbind_key in marker_keys:
        marker_keys.remove(unbind_key)
        db["marker_images"].update_one(
            {"imageId": image_id}, {"$set": {"match.markerKeys": marker_keys}}
        )
    else:
        db["marker_images"].delete_one({"imageId": image_id})
        GridFSImageStore(db).delete(entry["storageId"])

    bump_image_revision(db)
    refresh_image_catalog(db, force=True)
    write_activity(username, f"Deleted marker image {image_id}", db)
    flash("Image deleted.", "success")
    return redirect(request.referrer or url_for("markers.marker_catalog"))
```

Imports this needs in `markers.py`: `abort`, `request`, `send_file`,
`url_for`, `flash`, `redirect`, `session`; `write_activity` from
`flymanager.utils.mongo.activity`; `get_catalog` from `marker_catalog`; and the
new image modules. `limiter` is already imported at `markers.py:17`.

- [ ] **Step 5: Wire up app startup**

In `flymanager/app/__init__.py`:

1. Delete the `PHENOTYPE_IMAGE_LIBRARY_PATH` config line (around line 169).
2. Run `load_image_seed(db, GridFSImageStore(db))` at startup, next to wherever
   slice A loads its catalog. Wrap it so a failure logs and re-raises — a
   half-loaded image set is a startup failure per the spec.
3. Extend the existing marker-catalog `before_request` probe to also call
   `refresh_image_catalog(db)`. Reuse the same 30-second throttle rather than
   adding a second timer.
4. In the RQ worker's `_run` envelope (`jobs/tasks.py:33`, where
   `refresh_catalog(db)` is already called) and in
   `refresh_flybase_reference_data`, add `refresh_image_catalog(db)` —
   background jobs never run `before_request`.
5. Add a 413 handler. `MAX_CONTENT_LENGTH` is 8 MB app-wide
   (`__init__.py:171-173`), so anything above that is rejected by Werkzeug
   **before** the route's own 2 MB check runs, and the user would otherwise
   get a bare error page instead of the flash message the 2-8 MB path gives:

   ```python
   @app.errorhandler(413)
   def handle_payload_too_large(error):
       flash("That file is too large. Images must be 2 MB or smaller.", "error")
       return redirect(request.referrer or url_for("main.home")), 302
   ```

   Check whether a 413 handler already exists before adding a second.

- [ ] **Step 6: Remove the old serving route**

Delete `phenotype_reference_image` from `flymanager/app/routes/stock.py:210-222`
and its now-unused `send_file` / `Path` imports if nothing else uses them.

- [ ] **Step 7: Run the route tests, then the full suite**

Run: `python -m pytest tests/test_marker_image_routes.py -q`
Expected: PASS

Then the full command from Global Constraints.
Expected: no more than the 15 baseline failures.

- [ ] **Step 8: Verify the signature isolation invariant**

This is the slice's headline guarantee; assert it directly. Add to
`tests/test_marker_image_routes.py`:

```python
def test_image_upload_does_not_move_the_marker_catalog_signature(logged_in_client):
    from flymanager.utils.phenotypes.marker_catalog import get_catalog

    before = get_catalog()["signature"]
    logged_in_client.post(
        "/markers/Sb%5B1%5D/images",
        data={"image": (io.BytesIO(_png((9, 9, 9))), "p.png")},
        content_type="multipart/form-data",
    )
    assert get_catalog()["signature"] == before
```

Also confirm by inspection that no image code path calls
`rebuild_after_marker_change` or `derive_affected_tokens`:

```bash
grep -rn "rebuild_after_marker_change\|derive_affected_tokens" \
  flymanager/app/routes/markers.py flymanager/utils/phenotypes/image_*.py
```

Expected: matches only in marker-definition handlers, never in an image handler.

- [ ] **Step 9: Delete the old library — the irreversible step**

```bash
git rm -r data/phenotype_images
```

Then re-run the full suite. Expected: still no more than 15 failures.

If the parity test fails now, the seed is missing images the filesystem was
still supplying. Recover with:

```bash
git checkout HEAD~1 -- data/phenotype_images
python scripts/build_marker_image_seed.py
```

This works only because Task 5's builder vendors its own frozen manifest and
scan readers instead of importing them from `image_library.py`, which Task 7
rewrote. If the builder still imports `_image_entries`, the rollback is
already broken — fix that before running Step 9, not after.

- [ ] **Step 10: Update the operational docs**

In `BACKUP_AND_RECOVERY.md:78-105`, move shipped phenotype images from the
state-archive scope to the Mongo-archive scope. State the tradeoff explicitly:
image bytes now ride Mongo dumps (+~4.5 MB per archive), the state archive
shrinks by ~120 MB, and the committed seed means a fresh deploy still
self-populates without a restore.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: upload, serve and manage marker images; retire the filesystem library"
```

---

### Task 9: Marker detail UI

**Files:**
- Modify: the marker detail template from slice A (find it under
  `flymanager/app/templates/`; `grep -rln "marker" flymanager/app/templates/`)
- Modify: `flymanager/app/routes/markers.py` (pass images into the template)
- Test: `tests/test_marker_image_routes.py`

**Interfaces:**
- Consumes: `get_image_catalog()["by_marker_key"]` (Task 4), the routes from
  Task 8.
- Produces: no new Python interfaces.

- [ ] **Step 1: Write the failing test**

```python
def test_marker_detail_renders_its_images_and_an_upload_form(logged_in_client, seeded_image):
    key = seeded_image["match"]["markerKeys"][0]
    response = logged_in_client.get(f"/markers/{key}")
    assert response.status_code == 200
    assert f"/markers/images/{seeded_image['imageId']}".encode() in response.data
    assert b'name="image"' in response.data


def test_marker_detail_shows_credit_for_shipped_images(logged_in_client, seeded_image):
    key = seeded_image["match"]["markerKeys"][0]
    response = logged_in_client.get(f"/markers/{key}")
    assert seeded_image["display"]["credit"].encode() in response.data
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_marker_image_routes.py -q`
Expected: FAIL — the image markup is absent.

- [ ] **Step 3: Pass images into the detail view**

In the `marker_detail` handler, add:

```python
images = get_image_catalog()["by_marker_key"].get(key, [])
```

and pass `images=images` to `render_template`.

- [ ] **Step 4: Add the template block**

Follow the existing template's markup conventions — read the surrounding file
and match its classes and structure rather than introducing new styling.

```html
<section class="marker-images">
  <h3>Images</h3>
  <div class="marker-image-strip">
    {% for image in images %}
      <figure>
        <img src="{{ url_for('markers.serve_marker_image', image_id=image.imageId) }}"
             alt="{{ image.display.label }}" loading="lazy" width="200">
        <figcaption>
          {{ image.display.caption }}
          {% if image.display.credit %}<small>{{ image.display.credit }}</small>{% endif %}
          {% if image.origin == 'user' or session['username'] == 'admin' %}
            <form method="post"
                  action="{{ url_for('markers.delete_marker_image', image_id=image.imageId) }}">
              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
              <input type="hidden" name="marker_key" value="{{ marker.Key }}">
              <button type="submit">Delete</button>
            </form>
          {% endif %}
        </figcaption>
      </figure>
    {% else %}
      <p>No images yet.</p>
    {% endfor %}
  </div>

  <form method="post"
        action="{{ url_for('markers.upload_marker_image', key=marker.Key) }}"
        enctype="multipart/form-data">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <input type="file" name="image" accept="image/*" required>
    <input type="text" name="caption" placeholder="Caption (optional)">
    <button type="submit">Upload image</button>
    <small>PNG, JPEG, GIF or WebP, up to 2 MB. Images are converted to WebP.</small>
  </form>
</section>
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_marker_image_routes.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/templates flymanager/app/routes/markers.py tests/test_marker_image_routes.py
git commit -m "feat: manage marker images from the marker detail page"
```

---

### Task 10: Final verification

- [ ] **Step 1: Run the full suite three times under a randomized hash seed**

```bash
for i in 1 2 3; do
  PYTHONHASHSEED=random /Users/neurorishika/Projects/Rockefeller/Ruta/FlyManager/.venv/bin/python \
    -m pytest tests/ -q -p no:cacheprovider --ignore=tests/new_feature_exploration 2>&1 | tail -3
done
```

Expected: the same 15 baseline failures every run, identical names each time.
Any run that differs indicates hash-order dependence — fix it before finishing.

- [ ] **Step 2: Confirm the repository shrank**

```bash
du -sh data/markers/images
git status --short
```

Expected: `data/markers/images` in the 4-7 MB range, `data/phenotype_images`
gone, working tree clean.

- [ ] **Step 3: Confirm no dead references remain**

```bash
grep -rn "phenotype_images\|PHENOTYPE_IMAGE_LIBRARY_PATH\|relative_path" \
  flymanager/ scripts/ tests/ --include="*.py" --include="*.html"
```

Expected: hits only in `scripts/build_marker_image_seed.py` and
`scripts/capture_image_scoring_baseline.py`, which document the migration.
Anything in `flymanager/` is a leftover — remove it.

- [ ] **Step 4: Run the app and confirm images render**

Use the `run-flymanager` skill to start the stack, log in, open a stock with a
phenotype prediction, and confirm reference images display. Then open a marker
detail page, upload an image, and confirm it appears and is preferred over the
fuzzy match. Screenshot both.

- [ ] **Step 5: Report**

Summarize: seed size, entry count, test counts against baseline, and anything
deferred.

---

## Self-Review Notes

Spec coverage check against the design document:

| Spec section | Task |
|---|---|
| Part 1 — storage layer | 1 |
| Part 2 — entry model, dedup upsert | 4, 6 |
| Part 3 — seed, migration, scorer tiers | 5, 7 |
| Part 4 — compilation and freshness | 4, 8 (step 4) |
| Part 5 — matching | 7 |
| Part 6 — upload, serving, permissions | 8 |
| Part 7 — UI | 9 |
| Data model changes | 5, 6, 8 |
| Dependencies (Pillow, parity check) | 2 |
| Backup doc update | 8 (step 9) |
| Error handling | 2, 6, 8 |
| Testing strategy | 2, 3, 5, 6, 7, 8, 10 |

Known gap accepted deliberately: the spec calls for a `marker_images` unique
index on `imageId` and secondary indexes on `match.markerKeys` and `sha256`.
These belong with the project's other index declarations in
`ensure_mongo_indexes`; add them there during Task 8 Step 5 (app startup wiring) rather than as a
separate task.
