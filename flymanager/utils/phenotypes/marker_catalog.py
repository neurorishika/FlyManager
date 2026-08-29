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
    if kind == "balancer" and not isinstance(payload.get("default_markers", []), list):
        errors.append("balancer payload.default_markers must be a list")
    if kind == "construct_marker" and not isinstance(payload.get("overrides", {}), dict):
        errors.append("construct_marker payload.overrides must be an object")

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
        "known_balancer_symbols": set(),
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
        errors = validate_definition(document)
        if errors:
            snapshot["invalid_definitions"].append(
                {"Key": (document or {}).get("Key") if isinstance(document, dict) else None,
                 "errors": errors}
            )
            continue
        merged[str(document["Key"]).strip()] = document
    snapshot["definitions"] = merged

    for key, document in merged.items():
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

    snapshot["known_balancer_symbols"] = set(snapshot["balancers"]) | set(snapshot["balancer_aliases"])
    # Precomputed and immutable: these are read per token on the parsing hot
    # path, so they must not be rebuilt or copied on every lookup.
    snapshot["balancer_match_order"] = tuple(
        sorted(snapshot["known_balancer_symbols"], key=len, reverse=True))
    snapshot["gene_marker_symbols"] = frozenset(snapshot["gene_markers"])
    snapshot["allele_marker_tokens"] = frozenset(snapshot["allele_markers"])
    snapshot["probe_symbols"] = sorted(set(snapshot["probe_symbols"]))
    snapshot["signature"] = compute_marker_catalog_signature(snapshot)
    return snapshot


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
