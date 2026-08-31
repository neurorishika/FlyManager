"""Compiled, in-process marker definition catalog.

Marker definitions live in two layers: a version-controlled shipped file
(``data/markers/catalog.json``, never written at runtime) and a Mongo
``marker_definitions`` overlay holding user additions and explicit overrides
of shipped keys. This module compiles the two into a single indexed snapshot
carrying a content signature.

Resolution reads the snapshot and nothing else, which is what preserves the
phenotype pipeline's db-less contract: ``resolve_package_markers`` and the
``get_*`` accessors never take a database handle.
"""
import hashlib
import json
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CATALOG_PATH = REPO_ROOT / "data" / "markers" / "catalog.json"

MARKER_KINDS = (
    "gene_marker",
    "allele_marker",
    "alias",
    "balancer",
    "construct_marker",
)

# provenance is stored camelCase on the envelope but merged back into the
# emitted marker dict under the legacy snake_case names the pipeline reads.
PROVENANCE_FIELDS = (
    ("geneName", "gene_name"),
    ("flybaseId", "flybase_id"),
    ("referenceUrl", "reference_url"),
    ("source", "source"),
)

# Excluded from the signature: changing any of these has no effect on any
# prediction, so including them would invalidate every materialized cache for
# a no-op edit (re-saving a row unchanged, or an admin promoting it).
SIGNATURE_EXCLUDED_FIELDS = frozenset({
    "_id",
    "CreatedBy", "CreatedAt", "UpdatedBy", "UpdatedAt", "CuratedBy", "CuratedAt",
    "origin", "overridesShipped",
})


def load_shipped_catalog(path=None):
    """Read and structurally validate the shipped catalog file.

    Raises ValueError on anything malformed. A half-loaded marker set would
    silently produce wrong predictions and poison every cache it touched, so
    this is a hard failure, not a degradation.
    """
    catalog_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    try:
        payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read marker catalog at {catalog_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Marker catalog at {catalog_path} is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Marker catalog at {catalog_path} must be a JSON object")
    definitions = payload.get("definitions")
    if not isinstance(definitions, list):
        raise ValueError(f"Marker catalog at {catalog_path} must contain a 'definitions' list")

    return {
        "catalogVersion": int(payload.get("catalogVersion") or 0),
        "definitions": definitions,
    }


def _sorting_errors(sorting):
    """Problems with a definition's `sorting` section.

    compile_catalog calls float() on stabilityScore, and compilation has no
    per-row recovery: a non-numeric score there raises out of the compile and
    takes down every catalog read for every user. Definitions arrive from the
    marker API, so that row is reachable by any logged-in user -- validating
    here is what routes it to invalid_definitions instead.
    """
    if not isinstance(sorting, dict):
        return ["sorting must be an object"]

    errors = []
    score = sorting.get("stabilityScore")
    if score is not None:
        # bool is an int subclass, so it would otherwise pass as 1.0.
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            errors.append("sorting.stabilityScore must be a number")
        elif not 0 <= score <= 1:
            errors.append("sorting.stabilityScore must be between 0 and 1")

    notes = sorting.get("notes")
    if notes is not None and not isinstance(notes, list):
        errors.append("sorting.notes must be a list")
    return errors


def _string_list_errors(value, label):
    """A list-of-strings field, checked for both wrong type and the silent variant.

    A bare string is the dangerous case: it is iterable, so compilation accepts
    it and indexes it CHARACTER BY CHARACTER. `aliases: "CyO2"` used to register
    C, y, O and 2 as balancer symbols the genotype parser matches, producing
    wrong predictions under a perfectly valid catalog signature.
    """
    if value is None:
        return []
    if isinstance(value, str) or not isinstance(value, list):
        return [f"{label} must be a list of strings"]
    if any(not isinstance(item, str) for item in value):
        return [f"{label} must contain only strings"]
    return []


def _section_errors(document):
    """Type checks for every envelope section compile_catalog dereferences."""
    errors = []
    for name in ("imaging", "audit", "provenance", "expression"):
        if not isinstance(document.get(name, {}), dict):
            errors.append(f"{name} must be an object")

    imaging = document.get("imaging", {})
    if isinstance(imaging, dict):
        errors.extend(_string_list_errors(imaging.get("aliases"), "imaging.aliases"))

    match = document.get("match", {})
    if isinstance(match, dict):
        errors.extend(_string_list_errors(match.get("aliases"), "match.aliases"))

    payload = document.get("payload", {})
    if isinstance(payload, dict):
        errors.extend(_string_list_errors(
            payload.get("default_markers"), "payload.default_markers"))
    return errors


def validate_definition(document):
    """Return a list of human-readable problems; empty means valid."""
    if not isinstance(document, dict):
        return ["definition must be an object"]

    errors = []
    if not str(document.get("Key") or "").strip():
        errors.append("Key is required")

    kind = document.get("kind")
    if kind not in MARKER_KINDS:
        errors.append(f"kind must be one of {', '.join(MARKER_KINDS)}")

    payload = document.get("payload", {})
    if not isinstance(payload, dict):
        errors.append("payload must be an object")
        payload = {}
    if not isinstance(document.get("match", {}), dict):
        errors.append("match must be an object")

    if kind == "alias" and not str(payload.get("value") or "").strip():
        errors.append("alias payload.value is required")
    if kind == "construct_marker" and not isinstance(payload.get("overrides", {}), dict):
        errors.append("construct_marker payload.overrides must be an object")

    errors.extend(_sorting_errors(document.get("sorting", {})))
    errors.extend(_section_errors(document))

    try:
        json.dumps(document, sort_keys=True)
    except (TypeError, ValueError):
        errors.append("definition contains values that are not JSON-serializable")

    return errors


def _marker_payload(document):
    marker = dict(document.get("payload") or {})
    provenance = document.get("provenance") or {}
    for camel, snake in PROVENANCE_FIELDS:
        value = provenance.get(camel)
        if value not in (None, ""):
            marker[snake] = value
    return marker


def _display_label(document, marker):
    if document["kind"] == "construct_marker":
        overrides = (document.get("payload") or {}).get("overrides") or {}
        return str(overrides.get("display_label") or "")
    return str(marker.get("display_label") or document.get("Key") or "")


def _phenotype_key(document, marker):
    if document["kind"] == "construct_marker":
        overrides = (document.get("payload") or {}).get("overrides") or {}
        return str(overrides.get("phenotype_key") or "")
    return str(marker.get("phenotype_key") or document.get("Key") or "")


def _empty_snapshot():
    return {
        "catalogVersion": 0,
        "definitions": {},
        "invalid_definitions": [],
        "gene_markers": {},
        "allele_markers": {},
        "aliases": {},
        "aliases_by_target": {},
        "balancers": {},
        "balancer_aliases": {},
        "balancer_markers": {},
        "balancers_referencing": {},
        "known_balancer_symbols": frozenset(),
        "balancer_match_order": (),
        "gene_marker_symbols": frozenset(),
        "allele_marker_tokens": frozenset(),
        "construct_markers": {},
        "stability": {},
        "image_aliases": {},
        "probe_symbols": [],
        "signature": "",
    }


def compile_catalog(shipped, overlay_documents=()):
    """Merge shipped defaults with the Mongo overlay and build the indexes.

    Overlay documents win by ``Key``. A document that fails validation is
    skipped and recorded in ``invalid_definitions`` rather than raising: one
    bad user row must not be able to take the app down.
    """
    snapshot = _empty_snapshot()
    snapshot["catalogVersion"] = int((shipped or {}).get("catalogVersion") or 0)

    merged = {}
    for document in list((shipped or {}).get("definitions") or []) + list(overlay_documents or []):
        try:
            errors = validate_definition(document)
        except Exception as exc:
            # validate_definition itself reads the document, so a value that
            # misbehaves on access raises here rather than during indexing.
            errors = [f"could not be validated: {exc}"]
        if errors:
            snapshot["invalid_definitions"].append(
                {"Key": (document or {}).get("Key") if isinstance(document, dict) else None,
                 "errors": errors}
            )
            continue
        merged[str(document["Key"]).strip()] = document
    snapshot["definitions"] = merged

    for key, document in list(merged.items()):
        try:
            _index_definition(snapshot, key, document)
        except Exception as exc:
            # Belt and braces behind validate_definition. Validation cannot
            # enumerate every shape a future field might take, and a raise
            # here is uniquely expensive: the write has already committed, so
            # every process would serve a frozen catalog and every later save
            # would 500 in its post-write refresh, with nothing naming the row.
            snapshot["definitions"].pop(key, None)
            snapshot["invalid_definitions"].append(
                {"Key": key, "errors": [f"could not be indexed: {exc}"]})

    snapshot["known_balancer_symbols"] = frozenset(
        set(snapshot["balancers"]) | set(snapshot["balancer_aliases"]))
    # Precomputed and immutable: these are read per token on the parsing hot
    # path, so they must not be rebuilt or copied on every lookup.
    snapshot["balancer_match_order"] = tuple(
        sorted(snapshot["known_balancer_symbols"], key=lambda s: (-len(s), s)))
    snapshot["gene_marker_symbols"] = frozenset(snapshot["gene_markers"])
    snapshot["allele_marker_tokens"] = frozenset(snapshot["allele_markers"])
    snapshot["probe_symbols"] = sorted(set(snapshot["probe_symbols"]))
    snapshot["signature"] = compute_marker_catalog_signature(snapshot)
    return snapshot


def _index_definition(snapshot, key, document):
    """Fold one validated definition into every index the snapshot carries."""
    kind = document["kind"]
    marker = _marker_payload(document)

    if kind == "gene_marker":
        snapshot["gene_markers"][key] = marker
    elif kind == "allele_marker":
        snapshot["allele_markers"][key] = marker
    elif kind == "alias":
        snapshot["aliases"][key] = marker
        target = str(marker.get("value") or "")
        snapshot["aliases_by_target"].setdefault(target, set()).add(key)
    elif kind == "construct_marker":
        match = document.get("match") or {}
        snapshot["construct_markers"][str(match.get("geneStem") or "")] = {
            "key": key,
            "allele_prefix": str(match.get("allelePrefix") or "+"),
            "overrides": dict((document.get("payload") or {}).get("overrides") or {}),
        }
    elif kind == "balancer":
        match = document.get("match") or {}
        symbol = str(match.get("symbol") or key)
        metadata = {"symbol": symbol}
        metadata.update(marker)
        aliases = [str(alias) for alias in (match.get("aliases") or [])]
        if aliases:
            metadata["aliases"] = list(aliases)
        snapshot["balancers"][symbol] = metadata
        default_markers = list(metadata.get("default_markers") or [])
        snapshot["balancer_markers"][symbol] = default_markers
        for alias in aliases:
            snapshot["balancer_aliases"][alias] = symbol
        for marker_key in default_markers:
            snapshot["balancers_referencing"].setdefault(marker_key, set()).add(symbol)

    sorting = document.get("sorting") or {}
    if sorting.get("stabilityScore") is not None:
        label = _display_label(document, marker)
        if label:
            snapshot["stability"][label] = {
                "score": float(sorting["stabilityScore"]),
                "notes": list(sorting.get("notes") or []),
            }

    if (document.get("audit") or {}).get("isProbeMarker"):
        snapshot["probe_symbols"].append(
            str((document.get("audit") or {}).get("probeSymbol") or key)
        )

    image_aliases = list((document.get("imaging") or {}).get("aliases") or [])
    if image_aliases:
        lookup_key = _phenotype_key(document, marker)
        if lookup_key:
            snapshot["image_aliases"][lookup_key] = image_aliases

def _canonical_definition(document):
    return {
        key: value
        for key, value in document.items()
        if key not in SIGNATURE_EXCLUDED_FIELDS
    }


def compute_marker_catalog_signature(snapshot):
    """Deterministic content hash over catalogVersion plus every definition.

    Hashing the compiled set rather than just the overlay means a release that
    edits catalog.json without bumping catalogVersion still invalidates stale
    caches.
    """
    definitions = snapshot.get("definitions") or {}
    payload = {
        "catalogVersion": snapshot.get("catalogVersion", 0),
        "definitions": [
            _canonical_definition(definitions[key]) for key in sorted(definitions)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


_LOCK = threading.Lock()
_SNAPSHOT = None
# Serializes the whole read-compile-install sequence in refresh_catalog, so
# only one thread compiles at a time. Distinct from _LOCK (which only
# guards the snapshot pointer itself) because threading.Lock is not
# reentrant: refresh_catalog must hold this lock while it briefly takes
# _LOCK to install, and get_catalog()/set_catalog() must remain callable
# independently without ever needing _REFRESH_LOCK.
_REFRESH_LOCK = threading.Lock()


def get_catalog():
    """Return the current compiled snapshot, compiling shipped-only if needed.

    Never queries Mongo. Callers on the phenotype resolution path use this;
    the overlay is folded in out-of-band by refresh_catalog().
    """
    global _SNAPSHOT
    if _SNAPSHOT is None:
        with _LOCK:
            if _SNAPSHOT is None:
                _SNAPSHOT = compile_catalog(load_shipped_catalog(), [])
    return _SNAPSHOT


def set_catalog(snapshot):
    """Install a compiled snapshot. Used by refresh_catalog and by tests."""
    global _SNAPSHOT
    with _LOCK:
        _SNAPSHOT = snapshot
    return snapshot


def reset_catalog():
    """Drop the cached snapshot so the next get_catalog() recompiles."""
    global _SNAPSHOT, _SNAPSHOT_REVISION
    with _LOCK:
        _SNAPSHOT = None
        _SNAPSHOT_REVISION = None


MARKER_CATALOG_REVISION_KEY = "markerCatalogRevision"
MARKER_DEFINITIONS_COLLECTION = "marker_definitions"
DEFAULT_REFRESH_INTERVAL_SECONDS = 30

_SNAPSHOT_REVISION = None
_LAST_REVISION_CHECK = None


def read_catalog_revision(db):
    """Current catalog revision from the settings singleton.

    Reads the collection directly rather than going through
    flymanager.utils.mongo.settings.get_settings: that helper INSERTS a
    default settings document when none exists, and this runs on the
    request path where a probe must never write. It also avoids importing
    the mongo package, which has a circular-import cycle with the app
    package.
    """
    document = db["settings"].find_one({}) or {}
    return int(document.get(MARKER_CATALOG_REVISION_KEY) or 0)


def _overlay_documents(db):
    """Fetch overlay documents, dropping the Mongo _id.

    _id is excluded from the signature anyway, and carrying an ObjectId into
    the compiled snapshot serves no purpose. Everything else is passed
    through as stored: a document holding values that are not JSON types is
    invalid, and validate_definition rejects it so compile_catalog can
    record it in invalid_definitions rather than silently altering it.
    """
    documents = []
    for document in db[MARKER_DEFINITIONS_COLLECTION].find({}):
        document = dict(document)
        document.pop("_id", None)
        documents.append(document)
    return documents


def refresh_catalog(db, *, force=False):
    """Recompile the snapshot from Mongo when the stored revision has moved.

    The whole read-compile-install sequence is serialized on _REFRESH_LOCK.
    Without it two threads can install out of order -- a slow compile of an
    older revision overwriting a newer snapshot that already landed -- which
    would make the served catalog go backward instead of converging.

    A compile failure propagates with the previous snapshot left installed:
    the request path must never be left without a catalog, and a half-built
    one would be worse than a stale one.
    """
    global _SNAPSHOT, _SNAPSHOT_REVISION

    with _REFRESH_LOCK:
        revision = read_catalog_revision(db)
        if not force and _SNAPSHOT is not None and _SNAPSHOT_REVISION == revision:
            return _SNAPSHOT

        snapshot = compile_catalog(load_shipped_catalog(), _overlay_documents(db))

        # Assigns _SNAPSHOT directly rather than calling set_catalog(): that
        # helper takes _LOCK itself, and threading.Lock is not reentrant, so
        # calling it from inside this _REFRESH_LOCK block while also nesting
        # into _LOCK below would deadlock.
        with _LOCK:
            _SNAPSHOT = snapshot
            _SNAPSHOT_REVISION = revision
        return snapshot


def maybe_refresh_catalog(db, *, interval_seconds=DEFAULT_REFRESH_INTERVAL_SECONDS, now=None):
    """Probe the stored revision at most once per interval per process.

    Returns the snapshot when the probe ran, or None when it was throttled.

    The throttle check-then-set below is intentionally unlocked. It doesn't
    need to be: once refresh_catalog serializes properly, two threads both
    passing the throttle gate is harmless -- the second to enter
    refresh_catalog's critical section just re-reads the same revision,
    finds it already installed, and returns immediately. Locking the
    throttle here would also deadlock, since this function calls
    refresh_catalog, which takes _REFRESH_LOCK itself.
    """
    global _LAST_REVISION_CHECK

    current = time.monotonic() if now is None else float(now)
    if _LAST_REVISION_CHECK is not None and (current - _LAST_REVISION_CHECK) < interval_seconds:
        return None
    _LAST_REVISION_CHECK = current
    return refresh_catalog(db)


def reset_refresh_state():
    """Test hook: forget the snapshot revision and the throttle clock."""
    global _SNAPSHOT_REVISION, _LAST_REVISION_CHECK
    _SNAPSHOT_REVISION = None
    _LAST_REVISION_CHECK = None
