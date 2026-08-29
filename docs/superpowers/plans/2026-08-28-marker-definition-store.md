# Marker Definition Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace FlyManager's six hardcoded visual-marker sites with one dynamic marker definition store: a shipped JSON catalog, a Mongo overlay of user additions and overrides, and a compiled in-process snapshot that the db-less phenotype resolution path reads.

**Architecture:** Definitions live in a version-controlled `data/markers/catalog.json` (never written at runtime) overlaid by a `marker_definitions` Mongo collection. `marker_catalog.py` compiles the two layers into an indexed snapshot carrying a content signature. `visual_markers.py` becomes thin accessors over that snapshot instead of 922 lines of literals. Both materialized cache envelopes stamp the signature, and a marker write enqueues a rebuild scoped to the tokens it can actually affect.

**Tech Stack:** Python 3, Flask, pymongo, RQ/Redis background jobs, pytest, `tests/mongo_fakes.FakeDatabase`.

**Spec:** `docs/superpowers/specs/2026-08-17-marker-definition-store-design.md` — read it before Task 1. The plan argues from the spec; where they differ, the "Deviations from the spec" section below is authoritative and explains why.

## Global Constraints

- **Test command** (from repo root, `.venv` active):
  ```bash
  export MONGO_URI="mongodb://127.0.0.1:27017/?directConnection=true"
  export MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 SECRET_KEY=test-secret-key MAIL_SUPPRESS_SEND=1
  python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/new_feature_exploration
  ```
  Importing `flymanager.app` connects to Mongo at import time, so a reachable Mongo is required for anything that imports the app. `tests/new_feature_exploration/` always gets ignored.
- **Baseline failures:** the suite has 15-16 pre-existing failures unrelated to this work (the recorded figure has moved; measure, do not assume). Establish the baseline count before Task 1 (`git stash -u`, run, unstash) and do not let it increase. Never "fix" a pre-existing failure as part of a task; note it and move on.
- **The resolution path must stay db-less.** `resolve_package_markers`, `get_visual_marker`, `get_balancer_metadata`, `get_reviewed_marker_alias`, `parse_gene_package` and everything they call must never take or use a `db` handle. They read the compiled snapshot only.
- **`data/markers/catalog.json` is never written at runtime.** Only `scripts/generate_marker_catalog.py` and hand edits touch it. All runtime writes go to Mongo.
- **The three legacy accessor signatures are frozen:** `get_visual_marker(symbol, allele_spec=None, token=None)`, `get_balancer_metadata(symbol)`, `get_reviewed_marker_alias(alias_token)`. Their return values must stay byte-identical to today's, including which optional keys are absent.
- **Payload dicts are stored verbatim in snake_case.** See Deviations.
- **The builder-calling tests need FlyBase reference data.** Tasks 11, 13 and 16 call `build_stock_phenotype_cache` / `build_cross_phenotype_cache` for real, which runs `compute_flybase_pipeline_signature()` and a full prediction. Existing cache tests mostly fabricate envelopes instead, so there is no precedent proving the builders run green and fast in a bare checkout. Before starting Task 11, confirm `data/flybase/` is populated and time one builder call; if it is slow or missing, say so rather than silently weakening the tests to fabricated envelopes.
- Commit at the end of every task. Do not squash tasks together.

## Deviations from the spec

Three deliberate departures, each because the spec's sketch would have cost correctness or churn:

1. **`payload` is the legacy marker dict verbatim, in snake_case** — not the camelCase field list the spec sketches. The compiled snapshot must reproduce `get_visual_marker()`'s output exactly, including the fact that `w` has no `gene_name`/`flybase_id`/`reference_url` and only some rows have `homozygous_lethal`. A camelCase translation layer would need a per-field presence map to preserve that; storing the dict verbatim makes fidelity structural instead of tested-and-hoped. Envelope fields (`Key`, `kind`, `match`, `sorting`, `audit`, `imaging`, `expression`, `provenance`, `origin`) stay camelCase as specced. `provenance` still holds `geneName`/`flybaseId`/`referenceUrl`/`source` and is merged back into the emitted marker dict at compile time, omitting empty values.

2. **The signature hashes the whole compiled definition set, not just the overlay.** The spec hashes `catalogVersion` plus overlay documents. That misses a release that edits `catalog.json` without remembering to bump `catalogVersion` — exactly the silent-stale-prediction failure the signature exists to prevent. Hashing all definitions costs nothing (a few hundred rows) and closes the hole. Audit-trail fields (`CreatedBy`/`CreatedAt`/`UpdatedBy`/`UpdatedAt`/`CuratedBy`/`CuratedAt`/`_id`), plus `origin` and `overridesShipped`, are excluded so that re-saving a row unchanged, or promoting it, does not invalidate every cached prediction for a change with no behavioural effect.

3. **Stamping unaffected records uses a scan plus `bulk_write`, not `update_many`.** The spec's `update_many` filter has to assert "this record's cached genotype still equals its current genotype", which needs `$expr` — a new query operator in the hot path and in `tests/mongo_fakes.py`. Scanning with the existing `get_cached_*_phenotype(record, strict=True)` predicate reuses the exact staleness rule the rest of the codebase already trusts, adds no new operator, and costs one collection scan that the backfill was going to do anyway.

One thing the spec did not anticipate, found while verifying the code: **`PHENOTYPE_IMAGE_ALIASES` has a key with no marker row.** `"epistasis:w_mini_white_rescue"` is a phenotype key minted by `epistasis.py:69`, not a marker. Since epistasis stays Python (spec, Out of scope), that one entry stays a hardcoded residual in `image_library.py`. The other 23 keys are all phenotype keys of real marker rows and migrate cleanly. Task 8 covers this.

## File Structure

**New:**

| File | Responsibility |
|---|---|
| `flymanager/utils/phenotypes/marker_catalog.py` | Load shipped file, validate, compile shipped+overlay into an indexed snapshot, compute the signature, hold process state, refresh from Mongo |
| `flymanager/utils/phenotypes/marker_rebuild.py` | Affected-token derivation with reverse-reference closure, targeted rebuild, signature stamping |
| `flymanager/utils/mongo/marker_definitions.py` | CRUD + permissions + revision bump + activity for the `marker_definitions` collection |
| `flymanager/app/routes/markers.py` | `/markers` blueprint |
| `flymanager/app/templates/markers/list.html`, `detail.html` | Catalog UI |
| `scripts/generate_marker_catalog.py` | One-shot migration from the Python literals to `catalog.json` + the legacy fixture |
| `data/markers/catalog.json` | Shipped defaults (tracked) |
| `tests/fixtures/legacy_marker_dictionaries.json` | Frozen copy of today's literals, so the fidelity test outlives their deletion |

**Modified:** `visual_markers.py` (922 lines of literals → ~120 lines of accessors), `parser.py`, `resolver.py`, `construct_markers.py`, `image_library.py`, `predictor.py`, `constraints/marker_stability.py`, `constraints/balancer_selection.py`, `app/services/stock_standardization.py`, `phenotypes/data/examiner.py`, `phenotypes/data/balancer_ingest.py`, `mongo/db.py`, `app/__init__.py`, `app/jobs/tasks.py`, `tests/mongo_fakes.py`, `tests/test_phenotype_experiments.py`.

---

### Task 1: Catalog entity, compilation, and signature

**Files:**
- Create: `flymanager/utils/phenotypes/marker_catalog.py`
- Test: `tests/test_marker_catalog_compile.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MARKER_KINDS = ("gene_marker", "allele_marker", "alias", "balancer", "construct_marker")`
  - `DEFAULT_CATALOG_PATH: pathlib.Path`
  - `validate_definition(document) -> list[str]` (empty list means valid)
  - `load_shipped_catalog(path=None) -> dict` with keys `catalogVersion: int`, `definitions: list[dict]`
  - `compile_catalog(shipped, overlay_documents=()) -> dict` (the snapshot; keys listed in Step 3)
  - `compute_marker_catalog_signature(snapshot) -> str` (32-char hex)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_marker_catalog_compile.py`:

```python
import json

import pytest

from flymanager.utils.phenotypes.marker_catalog import (
    compile_catalog, compute_marker_catalog_signature, load_shipped_catalog,
    validate_definition)

GENE_ROW = {
    "Key": "Cy",
    "kind": "gene_marker",
    "match": {"symbol": "Cy"},
    "payload": {
        "body_part": "wing",
        "effect": "curly wings",
        "dominance": "dominant",
        "display_label": "Cy",
        "phenotype_key": "Cy",
        "chromosome": 2,
        "scoring_confidence": 0.95,
    },
    "sorting": {"stabilityScore": 0.95, "notes": ["Curly is reliable."]},
    "audit": {"isProbeMarker": True, "probeSymbol": ""},
    "imaging": {"aliases": ["cy", "cyo"], "images": []},
    "expression": {},
    "provenance": {"source": "manual_dictionary"},
    "origin": "shipped",
}

ALLELE_ROW = {
    "Key": "wg[Sp-1]",
    "kind": "allele_marker",
    "match": {"token": "wg[Sp-1]", "geneStem": "wg", "alleleSpec": "Sp-1"},
    "payload": {
        "gene_stem": "wg",
        "body_part": "wing",
        "effect": "spade-shaped notched wings",
        "dominance": "dominant",
        "display_label": "Sp",
        "phenotype_key": "wg_Sp",
        "chromosome": 2,
        "scoring_confidence": 0.78,
    },
    "sorting": {},
    "audit": {"isProbeMarker": True, "probeSymbol": "Sp"},
    "imaging": {"aliases": ["sp"], "images": []},
    "expression": {},
    "provenance": {"geneName": "wingless", "flybaseId": "FBgn0284084",
                   "source": "manual_dictionary"},
    "origin": "shipped",
}

ALIAS_ROW = {
    "Key": "Gla",
    "kind": "alias",
    "match": {"token": "Gla"},
    "payload": {"alias_type": "allele_token", "value": "wg[Gla-1]"},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {}, "origin": "shipped",
}

BALANCER_ROW = {
    "Key": "CyO",
    "kind": "balancer",
    "match": {"symbol": "CyO", "aliases": []},
    "payload": {"family": "CyO", "chromosome": 2,
                "default_markers": ["Cy"], "notes": []},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {"source": "bdsc_cheat_sheet"}, "origin": "shipped",
}

ALIASED_BALANCER_ROW = {
    "Key": "Binsc",
    "kind": "balancer",
    "match": {"symbol": "Binsc", "aliases": ["Binsn"]},
    "payload": {"family": "Binsc", "chromosome": 1,
                "default_markers": ["sc"], "notes": []},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {"source": "bdsc_cheat_sheet"}, "origin": "shipped",
}

CONSTRUCT_ROW = {
    "Key": "construct:w+",
    "kind": "construct_marker",
    "match": {"geneStem": "w", "allelePrefix": "+"},
    "payload": {"overrides": {
        "dominance": "dominant",
        "display_label": "mini-white",
        "effect": "pigmented eyes from construct marker",
        "phenotype_key": "mini_white",
        "mini_white": True,
    }},
    "sorting": {"stabilityScore": 0.58, "notes": ["dosage-sensitive"]},
    "audit": {}, "imaging": {"aliases": ["miniwhite", "w+"], "images": []},
    "expression": {}, "provenance": {}, "origin": "shipped",
}


def _shipped(definitions, version=1):
    return {"catalogVersion": version, "definitions": list(definitions)}


def test_gene_marker_payload_merges_provenance_and_omits_absent_fields():
    snapshot = compile_catalog(_shipped([GENE_ROW]), [])
    assert snapshot["gene_markers"]["Cy"] == {
        "body_part": "wing",
        "effect": "curly wings",
        "dominance": "dominant",
        "display_label": "Cy",
        "phenotype_key": "Cy",
        "chromosome": 2,
        "scoring_confidence": 0.95,
        "source": "manual_dictionary",
    }
    assert "gene_name" not in snapshot["gene_markers"]["Cy"]


def test_allele_marker_is_indexed_by_token_with_provenance():
    snapshot = compile_catalog(_shipped([ALLELE_ROW]), [])
    marker = snapshot["allele_markers"]["wg[Sp-1]"]
    assert marker["gene_stem"] == "wg"
    assert marker["gene_name"] == "wingless"
    assert marker["flybase_id"] == "FBgn0284084"
    assert "reference_url" not in marker


def test_alias_indexes_forward_and_by_target():
    snapshot = compile_catalog(_shipped([ALIAS_ROW]), [])
    assert snapshot["aliases"]["Gla"] == {"alias_type": "allele_token",
                                          "value": "wg[Gla-1]"}
    assert snapshot["aliases_by_target"]["wg[Gla-1]"] == {"Gla"}


def test_balancer_metadata_shape_markers_and_reverse_index():
    snapshot = compile_catalog(_shipped([GENE_ROW, BALANCER_ROW]), [])
    assert snapshot["balancers"]["CyO"] == {
        "symbol": "CyO", "family": "CyO", "chromosome": 2,
        "default_markers": ["Cy"], "notes": [], "source": "bdsc_cheat_sheet",
    }
    assert snapshot["balancer_markers"]["CyO"] == ["Cy"]
    assert snapshot["known_balancer_symbols"] == {"CyO"}
    assert snapshot["balancers_referencing"]["Cy"] == {"CyO"}


def test_balancer_aliases_are_indexed_and_listed_on_the_canonical_row():
    snapshot = compile_catalog(_shipped([ALIASED_BALANCER_ROW]), [])
    assert snapshot["balancer_aliases"] == {"Binsn": "Binsc"}
    assert snapshot["balancers"]["Binsc"]["aliases"] == ["Binsn"]
    assert snapshot["known_balancer_symbols"] == {"Binsc", "Binsn"}


def test_precomputed_hot_path_indexes():
    snapshot = compile_catalog(_shipped([GENE_ROW, ALLELE_ROW, ALIASED_BALANCER_ROW]), [])
    assert snapshot["gene_marker_symbols"] == frozenset({"Cy"})
    assert snapshot["allele_marker_tokens"] == frozenset({"wg[Sp-1]"})
    assert snapshot["balancer_match_order"] == ("Binsc", "Binsn")


def test_balancer_without_aliases_has_no_aliases_key():
    snapshot = compile_catalog(_shipped([BALANCER_ROW]), [])
    assert "aliases" not in snapshot["balancers"]["CyO"]


def test_construct_marker_is_indexed_by_gene_stem():
    snapshot = compile_catalog(_shipped([CONSTRUCT_ROW]), [])
    assert snapshot["construct_markers"]["w"] == {
        "key": "construct:w+",
        "allele_prefix": "+",
        "overrides": CONSTRUCT_ROW["payload"]["overrides"],
    }


def test_stability_is_keyed_by_display_label_including_constructs():
    snapshot = compile_catalog(_shipped([GENE_ROW, CONSTRUCT_ROW]), [])
    assert snapshot["stability"]["Cy"] == {"score": 0.95,
                                           "notes": ["Curly is reliable."]}
    assert snapshot["stability"]["mini-white"]["score"] == 0.58


def test_image_aliases_are_keyed_by_phenotype_key_only():
    snapshot = compile_catalog(_shipped([GENE_ROW, CONSTRUCT_ROW]), [])
    assert snapshot["image_aliases"]["Cy"] == ["cy", "cyo"]
    assert snapshot["image_aliases"]["mini_white"] == ["miniwhite", "w+"]
    assert "construct:w+" not in snapshot["image_aliases"]


def test_probe_symbols_use_probe_symbol_override_and_are_sorted():
    snapshot = compile_catalog(_shipped([GENE_ROW, ALLELE_ROW]), [])
    assert snapshot["probe_symbols"] == ["Cy", "Sp"]


def test_overlay_document_overrides_shipped_by_key():
    override = dict(GENE_ROW, origin="user",
                    payload=dict(GENE_ROW["payload"], effect="edited"))
    snapshot = compile_catalog(_shipped([GENE_ROW]), [override])
    assert snapshot["gene_markers"]["Cy"]["effect"] == "edited"
    assert snapshot["definitions"]["Cy"]["origin"] == "user"


def test_overlay_document_can_add_a_new_key():
    new_row = dict(GENE_ROW, Key="Zz", origin="user",
                   match={"symbol": "Zz"},
                   payload=dict(GENE_ROW["payload"], display_label="Zz",
                                phenotype_key="Zz"))
    snapshot = compile_catalog(_shipped([GENE_ROW]), [new_row])
    assert set(snapshot["gene_markers"]) == {"Cy", "Zz"}


def test_invalid_overlay_document_is_skipped_and_reported():
    snapshot = compile_catalog(_shipped([GENE_ROW]),
                               [{"Key": "", "kind": "gene_marker"}])
    assert snapshot["gene_markers"]["Cy"]["effect"] == "curly wings"
    assert len(snapshot["invalid_definitions"]) == 1
    assert "Key is required" in snapshot["invalid_definitions"][0]["errors"]


def test_validate_definition_rejects_unknown_kind_and_bad_alias():
    assert "kind must be one of" in " ".join(
        validate_definition({"Key": "x", "kind": "nope"}))
    assert "alias payload.value is required" in validate_definition(
        {"Key": "x", "kind": "alias", "payload": {}})


def test_signature_is_stable_and_content_sensitive():
    first = compile_catalog(_shipped([GENE_ROW, BALANCER_ROW]), [])
    second = compile_catalog(_shipped([BALANCER_ROW, GENE_ROW]), [])
    edited = compile_catalog(
        _shipped([dict(GENE_ROW, payload=dict(GENE_ROW["payload"], effect="x")),
                  BALANCER_ROW]), [])
    assert first["signature"] == second["signature"]
    assert first["signature"] != edited["signature"]
    assert len(first["signature"]) == 32


def test_signature_ignores_audit_trail_origin_and_override_marker():
    plain = compile_catalog(_shipped([GENE_ROW]), [])
    touched = compile_catalog(_shipped([dict(
        GENE_ROW,
        CreatedBy="alice",
        UpdatedAt="2026-01-01T00:00:00Z",
        origin="curated",
        overridesShipped={"key": "Cy", "shippedVersion": 1},
    )]), [])
    assert plain["signature"] == touched["signature"]


def test_signature_tracks_catalog_version():
    assert (compile_catalog(_shipped([GENE_ROW], version=1), [])["signature"]
            != compile_catalog(_shipped([GENE_ROW], version=2), [])["signature"])


def test_load_shipped_catalog_reads_a_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_shipped([GENE_ROW])), encoding="utf-8")
    loaded = load_shipped_catalog(path)
    assert loaded["catalogVersion"] == 1
    assert loaded["definitions"][0]["Key"] == "Cy"


def test_load_shipped_catalog_raises_on_malformed_json(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_shipped_catalog(path)


def test_load_shipped_catalog_raises_when_definitions_missing(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"catalogVersion": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_shipped_catalog(path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_marker_catalog_compile.py -q -p no:cacheprovider`
Expected: collection error — `ModuleNotFoundError: No module named 'flymanager.utils.phenotypes.marker_catalog'`

- [ ] **Step 3: Write the module**

Create `flymanager/utils/phenotypes/marker_catalog.py`:

```python
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

    # frozenset, not set: get_known_balancer_symbols() returns this without
    # copying, so a mutable value would let any caller corrupt the shared
    # process-wide snapshot. Matches gene_marker_symbols/allele_marker_tokens.
    snapshot["known_balancer_symbols"] = frozenset(
        set(snapshot["balancers"]) | set(snapshot["balancer_aliases"]))
    # Precomputed and immutable: these are read per token on the parsing hot
    # path, so they must not be rebuilt or copied on every lookup.
    snapshot["balancer_match_order"] = tuple(
        sorted(snapshot["known_balancer_symbols"], key=lambda symbol: (-len(symbol), symbol)))
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_marker_catalog_compile.py -q -p no:cacheprovider`
Expected: PASS (21 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/phenotypes/marker_catalog.py tests/test_marker_catalog_compile.py
git commit -m "feat: add marker catalog compilation and content signature"
```

---

### Task 2: Migrate the literals into the shipped catalog file

**Files:**
- Create: `scripts/generate_marker_catalog.py`
- Create (generated): `data/markers/catalog.json`, `tests/fixtures/legacy_marker_dictionaries.json`
- Test: `tests/test_marker_catalog_fidelity.py`

**Interfaces:**
- Consumes: `compile_catalog`, `load_shipped_catalog` from Task 1.
- Produces: `data/markers/catalog.json` (`catalogVersion: 1`), and the frozen legacy fixture that later tasks' fidelity test reads.

**Why the fixture exists:** Task 3 deletes the Python literals. A fidelity test that imports them would die with them. Freezing today's values into JSON keeps the "did we transcribe 922 lines correctly" guarantee alive permanently.

- [ ] **Step 1: Write the failing fidelity test**

Create `tests/test_marker_catalog_fidelity.py`:

```python
"""The shipped catalog must reproduce the pre-migration hardcoded dictionaries
exactly. tests/fixtures/legacy_marker_dictionaries.json is a frozen snapshot of
those dictionaries taken before they were deleted; regenerate it only if you
intend to change shipped marker behaviour."""
import json
from pathlib import Path

from flymanager.utils.phenotypes.marker_catalog import (compile_catalog,
                                                        load_shipped_catalog)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "legacy_marker_dictionaries.json"


def _legacy():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _snapshot():
    return compile_catalog(load_shipped_catalog(), [])


def test_gene_marker_dictionary_round_trips():
    assert _snapshot()["gene_markers"] == _legacy()["VISUAL_MARKER_DICTIONARY"]


def test_allele_marker_dictionary_round_trips():
    assert _snapshot()["allele_markers"] == _legacy()["ALLELE_VISUAL_MARKER_DICTIONARY"]


def test_reviewed_marker_aliases_round_trip():
    assert _snapshot()["aliases"] == _legacy()["REVIEWED_MARKER_ALIASES"]


def test_balancer_metadata_round_trips():
    assert _snapshot()["balancers"] == _legacy()["BALANCER_METADATA"]


def test_balancer_aliases_markers_and_known_symbols_round_trip():
    snapshot = _snapshot()
    legacy = _legacy()
    assert snapshot["balancer_aliases"] == legacy["BALANCER_ALIASES"]
    assert snapshot["balancer_markers"] == legacy["BALANCER_MARKERS"]
    assert snapshot["known_balancer_symbols"] == set(legacy["KNOWN_BALANCER_SYMBOLS"])


def test_probe_symbols_cover_every_critical_marker_including_sp():
    probes = set(_snapshot()["probe_symbols"])
    assert probes == set(_legacy()["CRITICAL_MARKERS"])
    assert "Sp" in probes
    assert len(probes) == 46


def test_stability_scores_and_notes_round_trip():
    stability = _snapshot()["stability"]
    legacy = _legacy()
    assert {label: entry["score"] for label, entry in stability.items()} == \
        legacy["MARKER_STABILITY_SCORES"]
    for label, notes in legacy["MARKER_NOTES"].items():
        assert stability[label]["notes"] == notes


def test_image_aliases_round_trip_except_the_epistasis_key():
    legacy = dict(_legacy()["PHENOTYPE_IMAGE_ALIASES"])
    residual = legacy.pop("epistasis:w_mini_white_rescue")
    assert residual, "the epistasis key is expected to exist and stay hardcoded"
    assert _snapshot()["image_aliases"] == legacy


def test_construct_marker_overrides_round_trip():
    construct_markers = _snapshot()["construct_markers"]
    assert set(construct_markers) == {"w", "y", "v"}
    assert construct_markers["w"]["overrides"]["phenotype_key"] == "mini_white"
    assert construct_markers["y"]["overrides"]["display_label"] == "y+"
    assert construct_markers["v"]["overrides"]["body_part"] == "eye"


def test_shipped_catalog_has_no_invalid_definitions():
    assert _snapshot()["invalid_definitions"] == []


def test_every_shipped_definition_is_marked_shipped():
    definitions = _snapshot()["definitions"].values()
    assert definitions
    assert {d["origin"] for d in definitions} == {"shipped"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_catalog_fidelity.py -q -p no:cacheprovider`
Expected: every test errors — `FileNotFoundError` / `ValueError: Unable to read marker catalog`

- [ ] **Step 3: Write the generator script**

Create `scripts/generate_marker_catalog.py`:

```python
"""One-shot migration: emit data/markers/catalog.json from the legacy hardcoded
marker dictionaries, plus tests/fixtures/legacy_marker_dictionaries.json so the
fidelity test survives those dictionaries' deletion.

Run from the repo root:

    python scripts/generate_marker_catalog.py

This script reads the Python literals in visual_markers.py, marker_stability.py
and image_library.py. Once those are deleted (Tasks 3, 6, 8) it stops running --
that is expected. From then on catalog.json is the source of truth, edited
directly for shipped changes or through /markers for everything else.

Every ownership decision (which definition owns a stability score, an image
alias list, a probe flag) is asserted, not guessed: an ambiguous or unclaimed
entry aborts the run rather than silently dropping data.
"""
import argparse
import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "data" / "markers" / "catalog.json"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "legacy_marker_dictionaries.json"
CATALOG_VERSION = 1

# Mirrors the if/elif chain at construct_markers.py:35-63 verbatim.
CONSTRUCT_MARKER_OVERRIDES = {
    "w": {
        "dominance": "dominant",
        "display_label": "mini-white",
        "effect": "pigmented eyes from construct marker",
        "phenotype_key": "mini_white",
        "mini_white": True,
    },
    "y": {
        "dominance": "dominant",
        "display_label": "y+",
        "effect": "yellow rescue marker",
        "phenotype_key": "y_plus",
    },
    "v": {
        "body_part": "eye",
        "dominance": "dominant",
        "display_label": "v+",
        "effect": "vermilion rescue marker",
        "phenotype_key": "v_plus",
    },
}

PROVENANCE_KEYS = (("geneName", "gene_name"), ("flybaseId", "flybase_id"),
                   ("referenceUrl", "reference_url"), ("source", "source"))


def _load_module(relative_path, name):
    """Load a module by path, so the pre-migration literals can be read even
    once the package-level names have moved.

    Note this does not fully avoid importing flymanager: marker_stability.py
    imports visual_markers, which pulls in the phenotypes package __init__ and
    hence pymongo. That chain opens no Mongo connection, so it is fine -- but
    if it ever does, read the two literals with ast.literal_eval instead of
    adding a database dependency to a migration script."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _split_provenance(entry):
    payload = {key: value for key, value in entry.items()
               if key not in {"gene_name", "flybase_id", "reference_url", "source"}}
    provenance = {}
    for camel, snake in PROVENANCE_KEYS:
        if entry.get(snake) not in (None, ""):
            provenance[camel] = entry[snake]
    return payload, provenance


def _envelope(key, kind, match, payload, provenance):
    return {
        "Key": key,
        "kind": kind,
        "match": match,
        "payload": payload,
        "sorting": {},
        "audit": {},
        "imaging": {"aliases": [], "images": []},
        "expression": {},
        "provenance": provenance,
        "origin": "shipped",
    }


def build_definitions(vm):
    definitions = []

    for symbol, entry in sorted(vm.VISUAL_MARKER_DICTIONARY.items()):
        payload, provenance = _split_provenance(entry)
        definitions.append(_envelope(symbol, "gene_marker", {"symbol": symbol},
                                     payload, provenance))

    for token, entry in sorted(vm.ALLELE_VISUAL_MARKER_DICTIONARY.items()):
        payload, provenance = _split_provenance(entry)
        gene_stem = payload.get("gene_stem", "")
        allele_spec = ""
        if "[" in token and token.endswith("]"):
            allele_spec = token[token.index("[") + 1:-1]
        definitions.append(_envelope(
            token, "allele_marker",
            {"token": token, "geneStem": gene_stem, "alleleSpec": allele_spec},
            payload, provenance))

    for token, entry in sorted(vm.REVIEWED_MARKER_ALIASES.items()):
        definitions.append(_envelope(token, "alias", {"token": token},
                                     dict(entry), {}))

    aliases_by_canonical = {}
    for alias, canonical in vm.BALANCER_ALIASES.items():
        aliases_by_canonical.setdefault(canonical, []).append(alias)

    for symbol, metadata in sorted(vm.BALANCER_METADATA.items()):
        payload = {key: value for key, value in metadata.items()
                   if key not in {"symbol", "source", "aliases"}}
        provenance = {"source": metadata["source"]} if metadata.get("source") else {}
        definitions.append(_envelope(
            symbol, "balancer",
            {"symbol": symbol, "aliases": sorted(aliases_by_canonical.get(symbol, []))},
            payload, provenance))

    for stem, overrides in CONSTRUCT_MARKER_OVERRIDES.items():
        definitions.append(_envelope(
            f"construct:{stem}+", "construct_marker",
            {"geneStem": stem, "allelePrefix": "+"},
            {"overrides": dict(overrides)}, {}))

    return definitions


def _labels_and_keys(definitions):
    by_label, by_phenotype_key = {}, {}
    for definition in definitions:
        if definition["kind"] == "construct_marker":
            source = definition["payload"]["overrides"]
        else:
            source = definition["payload"]
        label = source.get("display_label")
        phenotype_key = source.get("phenotype_key")
        if label:
            by_label.setdefault(label, []).append(definition)
        if phenotype_key:
            by_phenotype_key.setdefault(phenotype_key, []).append(definition)
    return by_label, by_phenotype_key


def _sole_owner(candidates, what, name):
    if len(candidates) != 1:
        raise SystemExit(
            f"Cannot place {what} {name!r}: {len(candidates)} candidate definitions"
        )
    return candidates[0]


def attach_sorting(definitions, stability_module):
    by_label, _ = _labels_and_keys(definitions)
    for label, score in stability_module.MARKER_STABILITY_SCORES.items():
        owner = _sole_owner(by_label.get(label, []), "stability score", label)
        owner["sorting"] = {
            "stabilityScore": score,
            "notes": list(stability_module._MARKER_NOTES.get(label, [])),
        }


def attach_imaging(definitions, image_module):
    _, by_phenotype_key = _labels_and_keys(definitions)
    residual = {}
    for lookup_key, aliases in image_module.PHENOTYPE_IMAGE_ALIASES.items():
        candidates = by_phenotype_key.get(lookup_key, [])
        if not candidates:
            residual[lookup_key] = list(aliases)
            continue
        owner = _sole_owner(candidates, "image aliases", lookup_key)
        owner["imaging"]["aliases"] = list(aliases)
    return residual


def attach_probe_flags(definitions, vm):
    by_key = {definition["Key"]: definition for definition in definitions}
    by_label, _ = _labels_and_keys(definitions)
    for symbol in vm.CRITICAL_MARKERS:
        owner = by_key.get(symbol)
        if owner is not None:
            owner["audit"] = {"isProbeMarker": True, "probeSymbol": ""}
            continue
        owner = _sole_owner(by_label.get(symbol, []), "probe marker", symbol)
        owner["audit"] = {"isProbeMarker": True, "probeSymbol": symbol}


def build_legacy_fixture(vm, stability_module, image_module):
    return {
        "VISUAL_MARKER_DICTIONARY": vm.VISUAL_MARKER_DICTIONARY,
        "ALLELE_VISUAL_MARKER_DICTIONARY": vm.ALLELE_VISUAL_MARKER_DICTIONARY,
        "REVIEWED_MARKER_ALIASES": vm.REVIEWED_MARKER_ALIASES,
        "BALANCER_METADATA": vm.BALANCER_METADATA,
        "BALANCER_ALIASES": vm.BALANCER_ALIASES,
        "BALANCER_MARKERS": vm.BALANCER_MARKERS,
        "KNOWN_BALANCER_SYMBOLS": sorted(vm.KNOWN_BALANCER_SYMBOLS),
        "CRITICAL_MARKERS": vm.CRITICAL_MARKERS,
        "MARKER_STABILITY_SCORES": stability_module.MARKER_STABILITY_SCORES,
        "MARKER_NOTES": stability_module._MARKER_NOTES,
        "PHENOTYPE_IMAGE_ALIASES": image_module.PHENOTYPE_IMAGE_ALIASES,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-path", default=str(CATALOG_PATH))
    parser.add_argument("--fixture-path", default=str(FIXTURE_PATH))
    args = parser.parse_args()

    vm = _load_module("flymanager/utils/phenotypes/visual_markers.py", "legacy_visual_markers")
    stability_module = _load_module(
        "flymanager/utils/constraints/marker_stability.py", "legacy_marker_stability")
    image_module = _load_module(
        "flymanager/utils/phenotypes/image_library.py", "legacy_image_library")

    definitions = build_definitions(vm)
    attach_sorting(definitions, stability_module)
    residual_image_aliases = attach_imaging(definitions, image_module)
    attach_probe_flags(definitions, vm)

    catalog_path = Path(args.catalog_path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(
        json.dumps({"catalogVersion": CATALOG_VERSION, "definitions": definitions},
                   indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    fixture_path = Path(args.fixture_path)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(
        json.dumps(build_legacy_fixture(vm, stability_module, image_module),
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {len(definitions)} definitions to {catalog_path}")
    print(f"Wrote legacy fixture to {fixture_path}")
    if residual_image_aliases:
        print("Image alias keys with no owning definition (keep these hardcoded):")
        for key, aliases in sorted(residual_image_aliases.items()):
            print(f"  {key}: {aliases}")


if __name__ == "__main__":
    main()
```

Note: `marker_stability.py` imports `get_visual_marker` from `visual_markers`, which at this point still has no external dependencies, so path-loading it works. If loading it raises, load `visual_markers` first under the name the import expects, or read the two literals with `ast.literal_eval`; do not add a pymongo dependency to this script.

- [ ] **Step 4: Run the generator**

Run: `python scripts/generate_marker_catalog.py`
Expected output includes `Wrote 104 definitions to .../data/markers/catalog.json` (48 gene + 10 allele + 7 alias + 36 balancer + 3 construct); if the total differs, one of those five groups was dropped -- find out which before continuing. Also expected:
```
Image alias keys with no owning definition (keep these hardcoded):
  epistasis:w_mini_white_rescue: ['miniwhite', 'mini-white', 'wplus', 'w+']
```
If the run aborts with "Cannot place ...", that is a real ambiguity in the source data — resolve it by making the ownership explicit in the script, never by dropping the entry.

- [ ] **Step 5: Run the fidelity test to verify it passes**

Run: `python -m pytest tests/test_marker_catalog_fidelity.py -q -p no:cacheprovider`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_marker_catalog.py data/markers/catalog.json \
        tests/fixtures/legacy_marker_dictionaries.json tests/test_marker_catalog_fidelity.py
git commit -m "feat: migrate hardcoded marker dictionaries into the shipped catalog"
```

---

### Task 3: Process snapshot state, and visual_markers becomes accessors

**Files:**
- Modify: `flymanager/utils/phenotypes/marker_catalog.py` (add process state)
- Modify: `flymanager/utils/phenotypes/visual_markers.py` (922 lines → ~130)
- Test: `tests/test_marker_catalog_state.py`, `tests/test_marker_accessors.py`

**Interfaces:**
- Consumes: `compile_catalog`, `load_shipped_catalog`, `DEFAULT_CATALOG_PATH` (Task 1); `data/markers/catalog.json` (Task 2).
- Produces:
  - `marker_catalog.get_catalog() -> dict` — current snapshot, compiling shipped-only on first call. **Never touches Mongo.**
  - `marker_catalog.set_catalog(snapshot)` / `marker_catalog.reset_catalog()` — test and refresh hooks.
  - `visual_markers.get_visual_marker(symbol, allele_spec=None, token=None)`, `get_balancer_metadata(symbol)`, `get_reviewed_marker_alias(alias_token)` — unchanged signatures and return values.
  - `visual_markers.get_gene_marker_symbols() -> set`, `get_allele_marker_tokens() -> set`, `get_gene_marker_dictionary() -> dict`, `get_allele_marker_dictionary() -> dict`, `get_reviewed_marker_aliases() -> dict`, `get_balancer_metadata_map() -> dict`, `get_balancer_aliases() -> dict`, `get_balancer_markers() -> dict`, `get_known_balancer_symbols() -> set`, `get_probe_marker_symbols() -> list`.
  - Temporary import-time compatibility names, deleted in Task 6: `CRITICAL_MARKERS`, `VISUAL_MARKER_DICTIONARY`, `ALLELE_VISUAL_MARKER_DICTIONARY`, `REVIEWED_MARKER_ALIASES`, `BALANCER_METADATA`, `BALANCER_ALIASES`, `BALANCER_MARKERS`, `KNOWN_BALANCER_SYMBOLS`.

**Why the compatibility names:** five modules import those constants today. Keeping them as import-time snapshots means this task changes only where the data comes from, with every existing test still passing unchanged, and the consumer conversions (Tasks 4-6) stay small and individually reviewable. They reproduce today's import-time-freeze behaviour exactly, so they are not a regression — but they are why the freeze is not actually fixed until Task 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_marker_catalog_state.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_get_catalog_compiles_the_shipped_file_without_mongo():
    snapshot = marker_catalog.get_catalog()
    assert snapshot["gene_markers"]["Cy"]["display_label"] == "Cy"
    assert snapshot["signature"]


def test_get_catalog_is_memoized():
    assert marker_catalog.get_catalog() is marker_catalog.get_catalog()


def test_set_catalog_replaces_the_snapshot():
    replacement = marker_catalog.compile_catalog(
        {"catalogVersion": 9, "definitions": []}, [])
    marker_catalog.set_catalog(replacement)
    assert marker_catalog.get_catalog()["catalogVersion"] == 9


def test_reset_catalog_forces_a_recompile():
    marker_catalog.set_catalog(marker_catalog.compile_catalog(
        {"catalogVersion": 9, "definitions": []}, []))
    marker_catalog.reset_catalog()
    assert marker_catalog.get_catalog()["catalogVersion"] == 1
```

Create `tests/test_marker_accessors.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog, visual_markers


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_get_visual_marker_returns_a_gene_marker_with_gene_stem():
    marker = visual_markers.get_visual_marker("Cy")
    assert marker["display_label"] == "Cy"
    assert marker["gene_stem"] == "Cy"


def test_get_visual_marker_prefers_an_allele_token():
    marker = visual_markers.get_visual_marker("wg", allele_spec="Sp-1")
    assert marker["display_label"] == "Sp"
    assert marker["allele_specific"] is True
    assert marker["allele_token"] == "wg[Sp-1]"


def test_get_visual_marker_accepts_an_explicit_token():
    marker = visual_markers.get_visual_marker("wg", token="wg[Gla-1]")
    assert marker["display_label"] == "Gla"


def test_get_visual_marker_returns_none_for_an_unknown_symbol():
    assert visual_markers.get_visual_marker("definitely-not-a-marker") is None


def test_get_visual_marker_returns_a_copy():
    first = visual_markers.get_visual_marker("Cy")
    first["display_label"] = "mutated"
    assert visual_markers.get_visual_marker("Cy")["display_label"] == "Cy"


def test_get_balancer_metadata_resolves_aliases_and_copies():
    assert visual_markers.get_balancer_metadata("Binsn")["symbol"] == "Binsc"
    assert visual_markers.get_balancer_metadata("CyO")["default_markers"] == \
        ["Cy", "pr", "cn"]
    assert visual_markers.get_balancer_metadata("nope") is None
    metadata = visual_markers.get_balancer_metadata("CyO")
    metadata["default_markers"].append("bogus")
    assert "bogus" not in visual_markers.get_balancer_metadata("CyO")["default_markers"]


def test_get_reviewed_marker_alias_matches_the_legacy_shape():
    assert visual_markers.get_reviewed_marker_alias("Gla") == {
        "alias_type": "allele_token", "value": "wg[Gla-1]"}
    assert visual_markers.get_reviewed_marker_alias("  Sco ")["value"] == "sna[Sco]"
    assert visual_markers.get_reviewed_marker_alias("nope") is None


def test_collection_accessors_expose_the_snapshot():
    assert "Cy" in visual_markers.get_gene_marker_symbols()
    assert "wg[Sp-1]" in visual_markers.get_allele_marker_tokens()
    assert "Binsn" in visual_markers.get_balancer_aliases()
    assert "CyO" in visual_markers.get_known_balancer_symbols()
    assert visual_markers.get_balancer_markers()["CyO"] == ["Cy", "pr", "cn"]
    assert "Sp" in visual_markers.get_probe_marker_symbols()
    assert len(visual_markers.get_probe_marker_symbols()) == 46


def test_accessors_follow_a_replaced_snapshot():
    edited = marker_catalog.load_shipped_catalog()
    overlay = [dict(
        next(d for d in edited["definitions"] if d["Key"] == "Cy"),
        origin="user",
        payload={"body_part": "wing", "effect": "edited effect",
                 "dominance": "dominant", "display_label": "Cy",
                 "phenotype_key": "Cy", "chromosome": 2,
                 "scoring_confidence": 0.95},
    )]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(edited, overlay))
    assert visual_markers.get_visual_marker("Cy")["effect"] == "edited effect"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_marker_catalog_state.py tests/test_marker_accessors.py -q -p no:cacheprovider`
Expected: `AttributeError: module 'flymanager.utils.phenotypes.marker_catalog' has no attribute 'reset_catalog'`

- [ ] **Step 3: Add process state to `marker_catalog.py`**

Append to `flymanager/utils/phenotypes/marker_catalog.py`:

```python
_LOCK = threading.Lock()
_SNAPSHOT = None


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
    global _SNAPSHOT
    with _LOCK:
        _SNAPSHOT = None
```

Add `import threading` to the module's imports.

- [ ] **Step 4: Rewrite `visual_markers.py`**

Replace the entire contents of `flymanager/utils/phenotypes/visual_markers.py` with:

```python
"""Marker accessors over the compiled catalog snapshot.

The marker dictionaries that used to be hardcoded in this file now live in
data/markers/catalog.json plus the marker_definitions Mongo overlay, compiled
by flymanager/utils/phenotypes/marker_catalog.py. This module keeps the three
legacy get_* accessors' exact signatures and return shapes, and adds accessors
for the structures that used to be exported as module-level dicts.

Nothing here touches Mongo: these are all reads of the in-process snapshot.
"""
from copy import deepcopy

from flymanager.utils.phenotypes.marker_catalog import get_catalog


def get_gene_marker_dictionary():
    """Copy of the gene-symbol -> marker mapping (was VISUAL_MARKER_DICTIONARY)."""
    return deepcopy(get_catalog()["gene_markers"])


def get_allele_marker_dictionary():
    """Copy of the allele-token -> marker mapping (was ALLELE_VISUAL_MARKER_DICTIONARY)."""
    return deepcopy(get_catalog()["allele_markers"])


def get_gene_marker_symbols():
    """Immutable set of known gene symbols.

    Returns the snapshot's precomputed frozenset rather than a fresh copy:
    membership tests are all the callers need, and this runs per token during
    a full backfill.
    """
    return get_catalog()["gene_marker_symbols"]


def get_allele_marker_tokens():
    """Immutable set of known allele tokens."""
    return get_catalog()["allele_marker_tokens"]


def get_reviewed_marker_aliases():
    """Copy of the reviewed alias table (was REVIEWED_MARKER_ALIASES)."""
    return deepcopy(get_catalog()["aliases"])


def get_balancer_metadata_map():
    """Copy of the balancer symbol -> metadata mapping (was BALANCER_METADATA)."""
    return deepcopy(get_catalog()["balancers"])


def get_balancer_aliases():
    """Copy of the balancer alias -> canonical symbol mapping."""
    return dict(get_catalog()["balancer_aliases"])


def get_balancer_markers():
    """Copy of the balancer symbol -> default marker keys mapping."""
    return deepcopy(get_catalog()["balancer_markers"])


def get_known_balancer_symbols():
    """Every balancer symbol and alias the parser should recognise."""
    return get_catalog()["known_balancer_symbols"]


def get_balancer_match_order():
    """Balancer symbols longest-first, precomputed on the snapshot."""
    return get_catalog()["balancer_match_order"]


def get_probe_marker_symbols():
    """Symbols flagged as FlyBase coverage probes (was CRITICAL_MARKERS)."""
    return list(get_catalog()["probe_symbols"])


def get_balancer_metadata(symbol):
    catalog = get_catalog()
    text = str(symbol or "").strip()
    canonical_symbol = catalog["balancer_aliases"].get(text, text)
    metadata = catalog["balancers"].get(canonical_symbol)
    if metadata is None:
        return None
    return deepcopy(metadata)


def get_visual_marker(symbol, allele_spec=None, token=None):
    catalog = get_catalog()
    allele_markers = catalog["allele_markers"]

    allele_key = None
    if token and token in allele_markers:
        allele_key = token
    elif symbol and allele_spec:
        candidate_key = f"{symbol}[{allele_spec}]"
        if candidate_key in allele_markers:
            allele_key = candidate_key

    if allele_key is not None:
        marker = deepcopy(allele_markers[allele_key])
        marker.setdefault("gene_stem", symbol)
        marker["allele_specific"] = True
        marker["allele_token"] = allele_key
        return marker

    entry = catalog["gene_markers"].get(symbol)
    if entry is None:
        return None
    marker = deepcopy(entry)
    marker["gene_stem"] = symbol
    return marker


def get_reviewed_marker_alias(alias_token):
    return deepcopy(get_catalog()["aliases"].get(str(alias_token or "").strip()))


# --- Temporary import-time compatibility shims -----------------------------
# Deleted in Task 6 once every consumer uses the accessors above. These
# reproduce the old import-time snapshot behaviour exactly, including the
# fact that they do not follow a later catalog refresh.
CRITICAL_MARKERS = get_probe_marker_symbols()
VISUAL_MARKER_DICTIONARY = get_gene_marker_dictionary()
ALLELE_VISUAL_MARKER_DICTIONARY = get_allele_marker_dictionary()
REVIEWED_MARKER_ALIASES = get_reviewed_marker_aliases()
BALANCER_METADATA = get_balancer_metadata_map()
BALANCER_ALIASES = get_balancer_aliases()
BALANCER_MARKERS = get_balancer_markers()
KNOWN_BALANCER_SYMBOLS = get_known_balancer_symbols()
```

- [ ] **Step 5: Run the new tests plus the whole phenotype suite**

Run:
```bash
python -m pytest tests/test_marker_catalog_state.py tests/test_marker_accessors.py \
  tests/test_marker_catalog_fidelity.py tests/test_marker_catalog_compile.py \
  tests/test_phenotype_experiments.py tests/test_constraints.py -q -p no:cacheprovider
```
Expected: the new files PASS; `test_phenotype_experiments.py` and `test_constraints.py` match the baseline failure count established before Task 1, with no new failures.

- [ ] **Step 6: Run the full suite**

Run the full command from Global Constraints.
Expected: failure count equal to the baseline.

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/phenotypes/marker_catalog.py \
        flymanager/utils/phenotypes/visual_markers.py \
        tests/test_marker_catalog_state.py tests/test_marker_accessors.py
git commit -m "refactor: serve marker lookups from the compiled catalog snapshot"
```

---

### Task 4: Unfreeze balancer recognition in the parser

**Files:**
- Modify: `flymanager/utils/phenotypes/parser.py:5-14,58-74,111-121`
- Test: `tests/test_parser_dynamic_balancers.py`

**Interfaces:**
- Consumes: `visual_markers.get_known_balancer_symbols()`, `get_balancer_aliases()`, `get_balancer_metadata()` (Task 3); `marker_catalog.set_catalog`, `compile_catalog`, `load_shipped_catalog`.
- Produces: `parser.known_balancer_symbols()`, `parser.balancer_match_order()`. `KNOWN_BALANCERS` and `BALANCER_MATCH_ORDER` are deleted.

**The bug this fixes:** `parser.py:13-14` computes `KNOWN_BALANCERS` and `BALANCER_MATCH_ORDER` at import time, so a balancer added after the module loads can never be recognised — the single most visible consequence of the hardcoded design.

- [ ] **Step 1: Write the failing test**

Create `tests/test_parser_dynamic_balancers.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.parser import parse_gene_package


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _install_extra_balancer():
    """Add a balancer to the catalog *after* the parser module was imported."""
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "ZZ7",
        "kind": "balancer",
        "match": {"symbol": "ZZ7", "aliases": ["ZZ7b"]},
        "payload": {"family": "ZZ7", "chromosome": 3,
                    "default_markers": ["Sb"], "notes": []},
        "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
        "provenance": {"source": "user"}, "origin": "user",
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))


def test_shipped_balancer_is_still_recognised():
    parsed = parse_gene_package("CyO")
    assert [b["symbol"] for b in parsed["balancers"]] == ["CyO"]
    assert parsed["balancers"][0]["default_markers"] == ["Cy", "pr", "cn"]


def test_shipped_balancer_alias_normalises():
    parsed = parse_gene_package("Binsn")
    assert parsed["balancers"][0]["symbol"] == "Binsc"


def test_user_balancer_added_after_import_is_recognised():
    assert parse_gene_package("ZZ7")["balancers"] == []
    _install_extra_balancer()
    parsed = parse_gene_package("ZZ7")
    assert [b["symbol"] for b in parsed["balancers"]] == ["ZZ7"]
    assert parsed["balancers"][0]["default_markers"] == ["Sb"]


def test_user_balancer_alias_added_after_import_normalises():
    _install_extra_balancer()
    assert parse_gene_package("ZZ7b")["balancers"][0]["symbol"] == "ZZ7"


def test_inversion_token_matches_longest_symbol_first():
    _install_extra_balancer()
    parsed = parse_gene_package("In(2LR)SM6a")
    assert parsed["balancers"][0]["symbol"] == "SM6a"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_parser_dynamic_balancers.py -q -p no:cacheprovider`
Expected: `test_user_balancer_added_after_import_is_recognised` FAILS with `assert [] == ['ZZ7']` — the import-time freeze.

- [ ] **Step 3: Make the parser read the snapshot**

In `flymanager/utils/phenotypes/parser.py`, replace the import block and the two frozen constants:

```python
from flymanager.utils.phenotypes.visual_markers import (get_balancer_aliases,
                                                        get_balancer_match_order,
                                                        get_balancer_metadata,
                                                        get_known_balancer_symbols)

GROUP_PAIRS = {"{": "}", "[": "]", "(": ")"}
CONSTRUCT_PREFIXES = ("P{", "PBac{", "Mi{", "TI{", "M{")
ALLELE_RE = re.compile(r"^(?P<gene>[A-Za-z0-9.+*()_-]+)\[(?P<allele>[^\]]+)\]$")


def known_balancer_symbols():
    """Balancer symbols and aliases from the current catalog snapshot.

    Recomputed per call rather than frozen at import: a user-defined balancer
    must be recognised without restarting the process.
    """
    return get_known_balancer_symbols()


def balancer_match_order():
    """Symbols longest-first, so In(2LR)SM6a matches SM6a before SM6.

    Read straight off the snapshot, which precomputes the ordering: this runs
    once per In(...) token and re-sorting 38 symbols each time was measurable
    during a full backfill. The precomputed tuple breaks length ties
    alphabetically so the order does not shift with PYTHONHASHSEED.
    """
    return get_balancer_match_order()
```

Then update the three users:

```python
def _is_balancer(token):
    if token in known_balancer_symbols():
        return True
    if token.startswith("In("):
        return any(symbol in token for symbol in balancer_match_order())
    return False


def _normalize_balancer_symbol(token):
    aliases = get_balancer_aliases()
    if token in aliases:
        return aliases[token]
    if token in known_balancer_symbols():
        return token
    for symbol in balancer_match_order():
        if symbol in token:
            return aliases.get(symbol, symbol)
    return token
```

And in `parse_gene_package`, drop the `BALANCER_MARKERS` fallback (the metadata is the only source now):

```python
        if _is_balancer(token):
            symbol = _normalize_balancer_symbol(token)
            metadata = get_balancer_metadata(symbol) or {}
            result["balancers"].append(
                {
                    "token": token,
                    "symbol": symbol,
                    "default_markers": list(metadata.get("default_markers", [])),
                    "metadata": metadata,
                }
            )
            continue
```

The old `metadata.get("default_markers", BALANCER_MARKERS.get(symbol, []))` fallback was dead: `BALANCER_MARKERS` is derived from `BALANCER_METADATA`, so whenever the metadata lookup succeeded the fallback was unreachable, and when it failed the fallback missed too.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_parser_dynamic_balancers.py tests/test_phenotype_experiments.py -q -p no:cacheprovider`
Expected: the new file PASSES (5 tests); `test_phenotype_experiments.py` at baseline.

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/phenotypes/parser.py tests/test_parser_dynamic_balancers.py
git commit -m "fix: recognise balancers from the live catalog instead of freezing at import"
```

---

### Task 5: Convert the remaining constant consumers

**Files:**
- Modify: `flymanager/utils/phenotypes/resolver.py:9-11,144-160`
- Modify: `flymanager/utils/constraints/balancer_selection.py:16-17,22-26`
- Modify: `flymanager/app/services/stock_standardization.py:15-17,396-398,432`
- Modify: `flymanager/utils/phenotypes/data/examiner.py` (import, `:117-119`, `:593-594`, and the `REVIEWED_MARKER_ALIASES` uses around `:637`)
- Modify: `tests/test_phenotype_experiments.py:32`
- Test: `tests/test_marker_consumers_follow_catalog.py`

**Interfaces:**
- Consumes: the Task 3 accessors.
- Produces: no new public API. After this task the only remaining references to the compatibility shims are in `visual_markers.py` itself.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_consumers_follow_catalog.py`:

```python
"""Every marker consumer must read the live snapshot, not an import-time copy."""
import pytest

from flymanager.utils.constraints.balancer_selection import \
    _candidate_documents
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.resolver import resolve_package_markers
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _install_user_balancer_and_marker():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [
        {
            "Key": "zz",
            "kind": "gene_marker",
            "match": {"symbol": "zz"},
            "payload": {"body_part": "wing", "effect": "zigzag wings",
                        "dominance": "dominant", "display_label": "zz",
                        "phenotype_key": "zz", "chromosome": 3,
                        "scoring_confidence": 0.8},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
        {
            "Key": "ZZ7",
            "kind": "balancer",
            "match": {"symbol": "ZZ7", "aliases": []},
            "payload": {"family": "ZZ7", "chromosome": 3,
                        "default_markers": ["zz"], "notes": []},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
    ]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))


def test_resolver_resolves_a_user_balancers_markers():
    _install_user_balancer_and_marker()
    resolved = resolve_package_markers("ZZ7")
    labels = [marker["display_label"] for marker in resolved["markers"]]
    assert labels == ["zz"]
    assert resolved["markers"][0]["balancer_symbol"] == "ZZ7"


def test_balancer_selection_sees_a_user_balancer():
    _install_user_balancer_and_marker()
    candidates = _candidate_documents(3, FakeDatabase({"balancer_definitions": []}))
    assert "ZZ7" in {candidate.get("symbol") for candidate in candidates}


def test_standardization_stops_flagging_a_token_once_it_is_a_marker():
    from flymanager.app.services.stock_standardization import \
        summarize_genotype_standardization

    before = summarize_genotype_standardization("zz")
    assert "zz" in before["topTokens"]

    _install_user_balancer_and_marker()
    after = summarize_genotype_standardization("zz")
    assert "zz" not in after["topTokens"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_consumers_follow_catalog.py -q -p no:cacheprovider`
Expected: all three FAIL — the consumers hold import-time copies.

- [ ] **Step 3: Convert `resolver.py`**

Change the import:

```python
from flymanager.utils.phenotypes.visual_markers import (
    get_balancer_metadata, get_reviewed_marker_alias, get_visual_marker)
```

and the balancer loop in `resolve_package_markers` (the `BALANCER_MARKERS` fallback is dead for the reason given in Task 4):

```python
    for balancer in parsed["balancers"]:
        metadata = get_balancer_metadata(balancer["symbol"]) or {}
        for marker_symbol in metadata.get("default_markers", []):
```

- [ ] **Step 4: Convert `balancer_selection.py`**

Change the import to `from flymanager.utils.phenotypes.visual_markers import (get_balancer_metadata, get_balancer_metadata_map)` and the loop head in `_candidate_documents`:

```python
    for symbol, metadata in get_balancer_metadata_map().items():
        if metadata.get("chromosome") != chromosome:
            continue
        candidates[symbol] = metadata
```

`get_balancer_metadata_map()` already returns a deep copy, so the `deepcopy(metadata)` call and the now-unused `deepcopy` import go away — check whether `deepcopy` is still used elsewhere in the file before removing the import.

- [ ] **Step 5: Convert `stock_standardization.py`**

Change the import to:

```python
from flymanager.utils.phenotypes.visual_markers import (
    get_allele_marker_tokens, get_gene_marker_symbols,
    get_reviewed_marker_alias)
```

In `review_stock_standardization`, replace the two set constructions:

```python
    known_gene_stems = get_gene_marker_symbols()
    known_allele_tokens = get_allele_marker_tokens()
```

and the alias lookup:

```python
        reviewed_alias = get_reviewed_marker_alias(token)
```

- [ ] **Step 6: Convert `examiner.py`**

Replace the `visual_markers` import with the accessors, then:

```python
def examine_flybase_directory(data_dir, markers=None):
    data_dir = Path(data_dir)
    markers = markers or get_probe_marker_symbols()
```

In `_audit_stock_standardization_rows`:

```python
    known_gene_stems = get_gene_marker_symbols()
    known_allele_tokens = get_allele_marker_tokens()
```

and replace `if token in REVIEWED_MARKER_ALIASES: replacement = REVIEWED_MARKER_ALIASES[token].get("value")` with:

```python
                reviewed_alias = get_reviewed_marker_alias(token)
                if reviewed_alias is not None:
                    replacement = reviewed_alias.get("value")
                else:
                    replacement = (alias_index.get(normalized) or {}).get("canonical_token")
```

Grep the file for any other use of the four constants and convert each the same way: `grep -n "VISUAL_MARKER_DICTIONARY\|ALLELE_VISUAL_MARKER_DICTIONARY\|REVIEWED_MARKER_ALIASES\|CRITICAL_MARKERS" flymanager/utils/phenotypes/data/examiner.py`

- [ ] **Step 7: Convert `tests/test_phenotype_experiments.py`**

Replace the import of `BALANCER_MARKERS` with `get_balancer_markers` and each use `BALANCER_MARKERS[...]` with `get_balancer_markers()[...]`.

- [ ] **Step 8: Run the tests**

Run: `python -m pytest tests/test_marker_consumers_follow_catalog.py tests/test_phenotype_experiments.py tests/test_constraints.py tests/test_standardization_cache.py -q -p no:cacheprovider`
Expected: the new file PASSES (3 tests); the rest at baseline.

- [ ] **Step 9: Commit**

```bash
git add flymanager/utils/phenotypes/resolver.py \
        flymanager/utils/constraints/balancer_selection.py \
        flymanager/app/services/stock_standardization.py \
        flymanager/utils/phenotypes/data/examiner.py \
        tests/test_phenotype_experiments.py \
        tests/test_marker_consumers_follow_catalog.py
git commit -m "refactor: read marker data through catalog accessors in every consumer"
```

---

### Task 6: Delete the compatibility shims

**Files:**
- Modify: `flymanager/utils/phenotypes/visual_markers.py` (delete the trailing shim block)
- Test: `tests/test_marker_accessors.py` (add one guard test)

- [ ] **Step 1: Prove nothing still imports the shims**

Run:
```bash
grep -rn "CRITICAL_MARKERS\|VISUAL_MARKER_DICTIONARY\|ALLELE_VISUAL_MARKER_DICTIONARY\|REVIEWED_MARKER_ALIASES\|BALANCER_METADATA\|BALANCER_ALIASES\|BALANCER_MARKERS\|KNOWN_BALANCER_SYMBOLS" \
  --include="*.py" flymanager/ tests/ scripts/
```
Expected: hits only in `flymanager/utils/phenotypes/visual_markers.py` (the shim block itself), `scripts/generate_marker_catalog.py` (which reads the pre-migration module by path and legitimately names them), and the fixture and fidelity test files that read the frozen JSON keys. Any other hit is a Task 5 miss — go fix it there before continuing.

- [ ] **Step 2: Write the guard test**

Append to `tests/test_marker_accessors.py`:

```python
def test_legacy_constant_shims_are_gone():
    """The shims froze marker data at import; nothing may depend on them again."""
    for name in (
        "CRITICAL_MARKERS",
        "VISUAL_MARKER_DICTIONARY",
        "ALLELE_VISUAL_MARKER_DICTIONARY",
        "REVIEWED_MARKER_ALIASES",
        "BALANCER_METADATA",
        "BALANCER_ALIASES",
        "BALANCER_MARKERS",
        "KNOWN_BALANCER_SYMBOLS",
    ):
        assert not hasattr(visual_markers, name), f"{name} should have been deleted"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_accessors.py::test_legacy_constant_shims_are_gone -q -p no:cacheprovider`
Expected: FAIL — `AssertionError: CRITICAL_MARKERS should have been deleted`

- [ ] **Step 4: Delete the shim block**

Remove everything in `visual_markers.py` from the `# --- Temporary import-time compatibility shims` comment to the end of the file.

- [ ] **Step 5: Run the full suite**

Run the full command from Global Constraints.
Expected: baseline failure count, no new failures. Any `ImportError: cannot import name ...` is a missed consumer — convert it the way Task 5 did.

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/phenotypes/visual_markers.py tests/test_marker_accessors.py
git commit -m "refactor: drop the import-time marker constant shims"
```

---

### Task 7: Marker stability scores come from the catalog

**Files:**
- Modify: `flymanager/utils/constraints/marker_stability.py:1-22,38-56`
- Modify: `flymanager/utils/constraints/__init__.py:5-6,10-11`
- Test: `tests/test_marker_stability_catalog.py`

**Sequencing trap:** `constraints/__init__.py:5-6` imports `MARKER_STABILITY_SCORES` and re-exports it in `__all__`, and `flymanager/utils/crossing/simulator.py` imports the `constraints` package. Deleting the constant without editing `__init__.py` breaks the import of nearly the whole suite with `ImportError: cannot import name 'MARKER_STABILITY_SCORES'`. Nothing outside `__init__.py` and `marker_stability.py` itself references the name (verified by grep), so removing it from both the import and `__all__` is the whole fix.

**Interfaces:**
- Consumes: `marker_catalog.get_catalog()["stability"]` — `{display_label: {"score": float, "notes": [str]}}`.
- Produces: no signature changes. `assess_marker_stability(marker_or_label, *, balancer_symbol=None)` and `score_sorting_markers(markers)` keep their behaviour.

**Careful:** the TM6B/Tb special case at `marker_stability.py:53-55` is a rule about a marker *in a context*, not a property of the marker, so it stays in Python — same reasoning as epistasis.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_stability_catalog.py`:

```python
import pytest

from flymanager.utils.constraints.marker_stability import (
    assess_marker_stability, score_sorting_markers)
from flymanager.utils.phenotypes import marker_catalog


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_shipped_scores_and_notes_are_unchanged():
    assessment = assess_marker_stability("Cy")
    assert assessment["score"] == 0.95
    assert assessment["stability_label"] == "stable"
    assert assessment["notes"] == [
        "Curly wings are treated as the most reliable dominant balancer marker."
    ]


def test_mini_white_label_still_resolves_through_the_marker_dict():
    assessment = assess_marker_stability({"mini_white": True, "gene_stem": "w"})
    assert assessment["marker"] == "mini-white"
    assert assessment["score"] == 0.58


def test_tb_is_downweighted_inside_tm6b_by_the_python_rule():
    assessment = assess_marker_stability("Tb", balancer_symbol="TM6B")
    assert assessment["score"] == 0.35
    assert any("revert" in note for note in assessment["notes"])


def test_unscored_marker_falls_back_to_its_scoring_confidence():
    assessment = assess_marker_stability({"gene_stem": "Sp", "display_label": "Sp",
                                          "token": "wg[Sp-1]"})
    assert assessment["score"] == 0.78


def test_unknown_marker_falls_back_to_the_default():
    assert assess_marker_stability("nothing-at-all")["score"] == 0.68


def test_a_user_defined_score_is_picked_up_without_a_restart():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zz",
        "kind": "gene_marker",
        "match": {"symbol": "zz"},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": "zz",
                    "phenotype_key": "zz", "chromosome": 3,
                    "scoring_confidence": 0.8},
        "sorting": {"stabilityScore": 0.2, "notes": ["User says this is flaky."]},
        "audit": {}, "imaging": {}, "expression": {},
        "provenance": {"source": "user"}, "origin": "user",
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))

    assessment = assess_marker_stability("zz")
    assert assessment["score"] == 0.2
    assert assessment["stability_label"] == "unstable"
    assert assessment["notes"] == ["User says this is flaky."]


def test_score_sorting_markers_still_aggregates():
    summary = score_sorting_markers([{"display_label": "Cy"}, {"display_label": "B"}])
    assert summary["average_score"] == 0.68
    assert summary["weakest_marker"]["marker"] == "B"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_stability_catalog.py -q -p no:cacheprovider`
Expected: `test_a_user_defined_score_is_picked_up_without_a_restart` FAILS with `assert 0.8 == 0.2` — the hardcoded table has no entry for `zz`, so it falls through to `scoring_confidence`.

- [ ] **Step 3: Read the scores from the catalog**

In `flymanager/utils/constraints/marker_stability.py`, delete `MARKER_STABILITY_SCORES` and `_MARKER_NOTES` and replace the imports and the two lookups:

```python
from flymanager.utils.phenotypes.marker_catalog import get_catalog
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

DEFAULT_STABILITY_SCORE = 0.68


def _stability_entry(label):
    return get_catalog()["stability"].get(label) or {}
```

Then in `flymanager/utils/constraints/__init__.py`, drop `MARKER_STABILITY_SCORES` from both the `marker_stability` import list and `__all__`, leaving `assess_marker_stability` and `score_sorting_markers`.

`_marker_label` is unchanged. In `assess_marker_stability`:

```python
def assess_marker_stability(marker_or_label, *, balancer_symbol=None):
    label = _marker_label(marker_or_label)
    entry = _stability_entry(label)
    score = entry.get("score")
    marker = marker_or_label if isinstance(marker_or_label, dict) else None

    if score is None and marker is not None:
        curated = get_visual_marker(marker.get("gene_stem"), allele_spec=marker.get("allele_spec"), token=marker.get("token"))
        if curated is not None:
            score = float(curated.get("scoring_confidence", DEFAULT_STABILITY_SCORE))

    if score is None:
        score = DEFAULT_STABILITY_SCORE

    resolved_balancer = str(balancer_symbol or (marker or {}).get("balancer_symbol") or "").strip()
    notes = list(entry.get("notes") or [])
    if label == "Tb" and resolved_balancer in {"TM6B", "TM6"}:
        score = min(score, 0.35)
        notes.append("TM6B explicitly keeps Tb because the marker can revert at high frequency.")
```

The rest of the function and `score_sorting_markers` are unchanged; replace the two other literal `0.68` defaults in `score_sorting_markers` with `DEFAULT_STABILITY_SCORE`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_marker_stability_catalog.py tests/test_constraints.py tests/test_crossing_simulator.py -q -p no:cacheprovider`
Expected: the new file PASSES (7 tests); the other two at baseline. An `ImportError` here means the `constraints/__init__.py` edit was missed.

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/constraints/marker_stability.py \
        flymanager/utils/constraints/__init__.py tests/test_marker_stability_catalog.py
git commit -m "refactor: source marker stability scores from the catalog"
```

---

### Task 8: Image aliases come from the catalog

**Files:**
- Modify: `flymanager/utils/phenotypes/image_library.py:11-36,166-175`
- Test: `tests/test_image_library_catalog_aliases.py`

**Interfaces:**
- Consumes: `marker_catalog.get_catalog()["image_aliases"]` — `{phenotype_key: [alias]}`.
- Produces: `image_library.EPISTASIS_IMAGE_ALIASES` (the one residual), and `image_library._phenotype_image_aliases()` merging catalog and residual. `PHENOTYPE_IMAGE_ALIASES` is deleted; `BODY_PART_ALIASES` stays — it is a vocabulary of anatomical synonyms, not marker data.

- [ ] **Step 1: Write the failing test**

Create `tests/test_image_library_catalog_aliases.py`:

```python
import pytest

from flymanager.utils.phenotypes import image_library, marker_catalog


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_shipped_aliases_come_from_the_catalog():
    aliases = image_library._marker_aliases(
        {"phenotype_key": "wg_Gla", "display_label": "Gla"})
    assert "gla" in aliases


def test_construct_marker_aliases_come_from_the_catalog():
    aliases = image_library._marker_aliases(
        {"phenotype_key": "mini_white", "display_label": "mini-white"})
    assert "miniwhite" in aliases
    assert "wplus" in aliases


def test_the_epistasis_key_stays_hardcoded():
    assert "epistasis:w_mini_white_rescue" in image_library.EPISTASIS_IMAGE_ALIASES
    aliases = image_library._marker_aliases(
        {"phenotype_key": "epistasis:w_mini_white_rescue",
         "display_label": "mini-white orange"})
    assert "miniwhite" in aliases


def test_body_part_aliases_are_still_hardcoded():
    assert image_library.BODY_PART_ALIASES["wing"] == {"wing", "wings"}


def test_a_user_marker_contributes_its_image_aliases():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "zz",
        "kind": "gene_marker",
        "match": {"symbol": "zz"},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": "zz",
                    "phenotype_key": "zz", "chromosome": 3,
                    "scoring_confidence": 0.8},
        "sorting": {}, "audit": {},
        "imaging": {"aliases": ["zigzag", "zz-wing"], "images": []},
        "expression": {}, "provenance": {"source": "user"}, "origin": "user",
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))

    aliases = image_library._marker_aliases({"phenotype_key": "zz", "display_label": "zz"})
    assert "zigzag" in aliases
    # _normalize_key maps "-" to "minus" (and "+" to "plus"), so "zz-wing"
    # normalizes to "zzminuswing", not "zzwing". That is pre-existing behaviour:
    # the shipped mini_white entry works around it by listing both spellings.
    assert "zzminuswing" in aliases


def test_legacy_alias_table_is_gone():
    assert not hasattr(image_library, "PHENOTYPE_IMAGE_ALIASES")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_image_library_catalog_aliases.py -q -p no:cacheprovider`
Expected: `test_the_epistasis_key_stays_hardcoded` FAILS (`AttributeError: ... has no attribute 'EPISTASIS_IMAGE_ALIASES'`), and `test_legacy_alias_table_is_gone` FAILS.

- [ ] **Step 3: Read aliases from the catalog**

In `flymanager/utils/phenotypes/image_library.py`, delete `PHENOTYPE_IMAGE_ALIASES` and add:

```python
from flymanager.utils.phenotypes.marker_catalog import get_catalog

# The one phenotype key with no marker definition behind it: epistasis.py
# mints it at runtime for the mini-white-rescues-white rule. Epistasis rules
# stay in Python (see the spec's Out of scope), so its image aliases stay here.
EPISTASIS_IMAGE_ALIASES = {
    "epistasis:w_mini_white_rescue": ["miniwhite", "mini-white", "wplus", "w+"],
}


def _phenotype_image_aliases():
    aliases = dict(get_catalog()["image_aliases"])
    aliases.update(EPISTASIS_IMAGE_ALIASES)
    return aliases
```

and in `_marker_aliases`:

```python
    alias_table = _phenotype_image_aliases()
    for lookup_key in (marker.get("phenotype_key"), marker.get("display_label")):
        for alias in alias_table.get(str(lookup_key), []):
            normalized = _normalize_key(alias)
            if normalized:
                aliases.add(normalized)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_image_library_catalog_aliases.py tests/test_phenotype_routes.py -q -p no:cacheprovider`
Expected: the new file PASSES (6 tests); `test_phenotype_routes.py` at baseline.

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/phenotypes/image_library.py tests/test_image_library_catalog_aliases.py
git commit -m "refactor: source phenotype image aliases from the catalog"
```

---

### Task 9: Construct marker overrides come from the catalog

**Files:**
- Modify: `flymanager/utils/phenotypes/construct_markers.py:5,23-63`
- Test: `tests/test_construct_markers_catalog.py`

**Interfaces:**
- Consumes: `marker_catalog.get_catalog()["construct_markers"]` — `{gene_stem: {"key": str, "allele_prefix": str, "overrides": dict}}`.
- Produces: `construct_markers.construct_marker_pattern()` returning a compiled regex built from the catalog's gene stems. `extract_construct_marker_symbols(construct_token)` and `extract_construct_markers(construct_token)` keep their signatures.

**Why the regex must move too:** `CONSTRUCT_MARKER_RE` hardcodes `[wyv]`. Leaving it frozen would mean a user-defined construct marker is defined but never matched — worse than not supporting it, because the definition would look active in the UI.

- [ ] **Step 1: Write the failing test**

Create `tests/test_construct_markers_catalog.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.construct_markers import (
    extract_construct_marker_symbols, extract_construct_markers)


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_mini_white_override_is_unchanged():
    markers = extract_construct_markers("P{UAS-GFP}attP40, w[+mC]")
    assert len(markers) == 1
    marker = markers[0]
    assert marker["display_label"] == "mini-white"
    assert marker["phenotype_key"] == "mini_white"
    assert marker["mini_white"] is True
    assert marker["dominance"] == "dominant"
    assert marker["allele_spec"] == "+mC"
    assert marker["source"] == "construct_marker"
    assert marker["construct_type"] == "P"


def test_y_and_v_overrides_are_unchanged():
    y_marker = extract_construct_markers("P{x}y[+t7.7]")[0]
    assert (y_marker["display_label"], y_marker["phenotype_key"]) == ("y+", "y_plus")
    v_marker = extract_construct_markers("P{x}v[+t1.8]")[0]
    assert (v_marker["display_label"], v_marker["body_part"]) == ("v+", "eye")


def test_symbols_are_deduplicated():
    assert extract_construct_marker_symbols("P{x}w[+mC] w[+mC]") == \
        [{"gene_stem": "w", "allele_spec": "+mC"}]


def test_a_user_defined_construct_marker_is_matched_and_applied():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [
        {
            "Key": "ry",
            "kind": "gene_marker",
            "match": {"symbol": "ry"},
            "payload": {"body_part": "eye", "effect": "rosy eyes",
                        "dominance": "recessive", "display_label": "ry",
                        "phenotype_key": "ry", "chromosome": 3,
                        "scoring_confidence": 0.8},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
        {
            "Key": "construct:ry+",
            "kind": "construct_marker",
            "match": {"geneStem": "ry", "allelePrefix": "+"},
            "payload": {"overrides": {"dominance": "dominant",
                                      "display_label": "ry+",
                                      "effect": "rosy rescue marker",
                                      "phenotype_key": "ry_plus"}},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
    ]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))

    markers = extract_construct_markers("P{x}ry[+t7.2]")
    assert [marker["display_label"] for marker in markers] == ["ry+"]
    assert markers[0]["phenotype_key"] == "ry_plus"


def test_a_stem_with_no_base_marker_is_skipped():
    assert extract_construct_markers("P{x}q[+]") == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_construct_markers_catalog.py -q -p no:cacheprovider`
Expected: `test_a_user_defined_construct_marker_is_matched_and_applied` FAILS with `assert [] == ['ry+']` — `[wyv]` does not match `ry`.

- [ ] **Step 3: Build the pattern and overrides from the catalog**

Rewrite `flymanager/utils/phenotypes/construct_markers.py`:

```python
import re

from flymanager.utils.phenotypes.marker_catalog import get_catalog
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

CONSTRUCT_PREFIX_RE = re.compile(r"^(?P<kind>[A-Za-z0-9]+)\{")


def construct_marker_pattern():
    """Regex matching any catalogued construct-marker stem plus its allele prefix.

    Built per call from the catalog rather than frozen at import, so a
    user-defined construct marker is matched as soon as it is saved. Stems are
    sorted longest-first so a longer stem is never shadowed by a prefix of it.
    """
    entries = get_catalog()["construct_markers"]
    if not entries:
        return None
    alternatives = "|".join(
        re.escape(stem) for stem in sorted(entries, key=len, reverse=True) if stem
    )
    if not alternatives:
        return None
    return re.compile(rf"(?P<stem>{alternatives})\[(?P<allele>\+[^\]]*)\]")


def extract_construct_marker_symbols(construct_token):
    pattern = construct_marker_pattern()
    if pattern is None:
        return []

    markers = []
    seen = set()
    for match in pattern.finditer(construct_token or ""):
        symbol = match.group("stem")
        allele = match.group("allele")
        key = (symbol, allele)
        if key in seen:
            continue
        seen.add(key)
        markers.append({"gene_stem": symbol, "allele_spec": allele})
    return markers


def extract_construct_markers(construct_token):
    construct_markers = []
    entries = get_catalog()["construct_markers"]
    construct_type_match = CONSTRUCT_PREFIX_RE.match(construct_token or "")
    construct_type = construct_type_match.group("kind") if construct_type_match else "construct"

    for marker in extract_construct_marker_symbols(construct_token):
        stem = marker["gene_stem"]
        allele_spec = marker["allele_spec"]
        base_marker = get_visual_marker(stem)
        if base_marker is None:
            continue

        overrides = (entries.get(stem) or {}).get("overrides") or {}
        if overrides:
            base_marker.update(overrides)

        base_marker.update(
            {
                "allele_spec": allele_spec,
                "construct_token": construct_token,
                "construct_type": construct_type,
                "source": "construct_marker",
            }
        )
        construct_markers.append(base_marker)

    return construct_markers
```

Note the `allele_prefix` on a catalog entry is documentation for the UI; the regex already requires a leading `+`, matching today's behaviour. A construct-marker definition with a different prefix is out of scope for this slice — validate it as `"+"` if you want, but do not silently ignore a different value.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_construct_markers_catalog.py tests/test_phenotype_experiments.py -q -p no:cacheprovider`
Expected: the new file PASSES (5 tests); `test_phenotype_experiments.py` at baseline.

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/phenotypes/construct_markers.py tests/test_construct_markers_catalog.py
git commit -m "refactor: build construct marker matching from the catalog"
```

---

### Task 10: Overlay refresh and multi-process freshness

**Files:**
- Modify: `flymanager/utils/phenotypes/marker_catalog.py` (add refresh)
- Modify: `flymanager/app/__init__.py:262-264` (add a `before_request` hook)
- Test: `tests/test_marker_catalog_refresh.py`

**Interfaces:**
- Consumes: `get_settings(db)` / `update_settings(updates, db)` from `flymanager.utils.mongo.settings`; `FakeDatabase`.
- Produces:
  - `marker_catalog.MARKER_CATALOG_REVISION_KEY = "markerCatalogRevision"`
  - `marker_catalog.read_catalog_revision(db) -> int`
  - `marker_catalog.refresh_catalog(db, *, force=False) -> dict` — recompiles from Mongo when the stored revision moved or `force`; installs and returns the snapshot.
  - `marker_catalog.maybe_refresh_catalog(db, *, interval_seconds=30, now=None) -> dict | None` — throttled probe; returns the snapshot when it checked, `None` when it skipped.
  - `marker_catalog.reset_refresh_state()` — test hook, also resets the throttle clock.

**Design note:** `markerCatalogRevision` lives in Mongo and is authoritative. Each process holds the revision its snapshot was built from and re-checks at most once per interval, so every worker converges within that window instead of each holding a divergent lazy cache. Background rebuild jobs call `refresh_catalog(db, force=True)` rather than waiting.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_catalog_refresh.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog, visual_markers
from tests.mongo_fakes import FakeDatabase

USER_MARKER = {
    "Key": "zz",
    "kind": "gene_marker",
    "match": {"symbol": "zz"},
    "payload": {"body_part": "wing", "effect": "zigzag wings",
                "dominance": "dominant", "display_label": "zz",
                "phenotype_key": "zz", "chromosome": 3,
                "scoring_confidence": 0.8},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {"source": "user"}, "origin": "user",
}


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _db(definitions=(), revision=0):
    return FakeDatabase({
        "marker_definitions": list(definitions),
        "settings": [{"markerCatalogRevision": revision}],
    })


def test_read_catalog_revision_defaults_to_zero():
    assert marker_catalog.read_catalog_revision(FakeDatabase({"settings": [{}]})) == 0


def test_refresh_folds_the_overlay_into_the_snapshot():
    snapshot = marker_catalog.refresh_catalog(_db([USER_MARKER], revision=1))
    assert snapshot["gene_markers"]["zz"]["effect"] == "zigzag wings"
    assert visual_markers.get_visual_marker("zz")["display_label"] == "zz"


def test_refresh_is_a_noop_when_the_revision_has_not_moved():
    db = _db([USER_MARKER], revision=1)
    first = marker_catalog.refresh_catalog(db)
    db["marker_definitions"].delete_many({})
    second = marker_catalog.refresh_catalog(db)
    assert second is first, "unchanged revision must not trigger a recompile"


def test_force_recompiles_even_at_the_same_revision():
    db = _db([USER_MARKER], revision=1)
    marker_catalog.refresh_catalog(db)
    db["marker_definitions"].delete_many({})
    snapshot = marker_catalog.refresh_catalog(db, force=True)
    assert "zz" not in snapshot["gene_markers"]


def test_a_revision_bump_from_another_process_is_picked_up():
    db = _db([], revision=1)
    marker_catalog.refresh_catalog(db)
    db["marker_definitions"].insert_one(dict(USER_MARKER))
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 2}})
    assert "zz" in marker_catalog.refresh_catalog(db)["gene_markers"]


def test_maybe_refresh_throttles_within_the_interval():
    db = _db([USER_MARKER], revision=1)
    assert marker_catalog.maybe_refresh_catalog(db, now=1000.0) is not None
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 2}})
    db["marker_definitions"].delete_many({})
    assert marker_catalog.maybe_refresh_catalog(db, now=1010.0) is None
    assert "zz" in marker_catalog.get_catalog()["gene_markers"]


def test_maybe_refresh_rechecks_after_the_interval():
    db = _db([USER_MARKER], revision=1)
    marker_catalog.maybe_refresh_catalog(db, now=1000.0)
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 2}})
    db["marker_definitions"].delete_many({})
    assert marker_catalog.maybe_refresh_catalog(db, now=1031.0) is not None
    assert "zz" not in marker_catalog.get_catalog()["gene_markers"]


def test_a_failed_recompile_leaves_the_previous_snapshot_installed():
    class Boom:
        def __getitem__(self, name):
            raise RuntimeError("mongo is down")

    marker_catalog.refresh_catalog(_db([USER_MARKER], revision=1))
    before = marker_catalog.get_catalog()
    with pytest.raises(RuntimeError):
        marker_catalog.refresh_catalog(Boom(), force=True)
    assert marker_catalog.get_catalog() is before


def test_an_invalid_overlay_row_is_skipped_not_fatal():
    snapshot = marker_catalog.refresh_catalog(
        _db([USER_MARKER, {"Key": "", "kind": "gene_marker"}], revision=1))
    assert "zz" in snapshot["gene_markers"]
    assert len(snapshot["invalid_definitions"]) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_catalog_refresh.py -q -p no:cacheprovider`
Expected: `AttributeError: module ... has no attribute 'reset_refresh_state'`

- [ ] **Step 3: Add refresh to `marker_catalog.py`**

Append (and add `import time` to the imports):

```python
MARKER_CATALOG_REVISION_KEY = "markerCatalogRevision"
MARKER_DEFINITIONS_COLLECTION = "marker_definitions"
DEFAULT_REFRESH_INTERVAL_SECONDS = 30

_SNAPSHOT_REVISION = None
_LAST_REVISION_CHECK = None


def read_catalog_revision(db):
    from flymanager.utils.mongo.settings import get_settings

    return int((get_settings(db) or {}).get(MARKER_CATALOG_REVISION_KEY) or 0)


def refresh_catalog(db, *, force=False):
    """Recompile the snapshot from Mongo when the stored revision has moved.

    A compile failure propagates with the previous snapshot left installed:
    the request path must never be left without a catalog, and a half-built
    one would be worse than a stale one.
    """
    global _SNAPSHOT_REVISION

    revision = read_catalog_revision(db)
    if not force and _SNAPSHOT is not None and _SNAPSHOT_REVISION == revision:
        return _SNAPSHOT

    overlay = list(db[MARKER_DEFINITIONS_COLLECTION].find({}))
    snapshot = compile_catalog(load_shipped_catalog(), overlay)
    set_catalog(snapshot)
    _SNAPSHOT_REVISION = revision
    return snapshot


def maybe_refresh_catalog(db, *, interval_seconds=DEFAULT_REFRESH_INTERVAL_SECONDS, now=None):
    """Probe the stored revision at most once per interval per process.

    Returns the snapshot when the probe ran, or None when it was throttled.
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
```

Also update `reset_catalog()` to clear `_SNAPSHOT_REVISION`, so a reset cannot leave the module thinking a shipped-only snapshot is at some Mongo revision.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_marker_catalog_refresh.py -q -p no:cacheprovider`
Expected: PASS (9 tests)

- [ ] **Step 5: Wire the `before_request` hook**

In `flymanager/app/__init__.py`, next to `ensure_csp_nonce`:

```python
    @app.before_request
    def refresh_marker_catalog_if_stale():
        """Converge this worker's marker catalog with Mongo, at most once per
        interval. A failure here must never fail the request: the previous
        snapshot stays installed and is at most one interval stale."""
        from flymanager.utils.phenotypes.marker_catalog import \
            maybe_refresh_catalog

        try:
            maybe_refresh_catalog(db)
        except Exception as exc:
            app.logger.warning("Unable to refresh the marker catalog: %s", exc)
```

- [ ] **Step 6: Fail fast at startup on a malformed shipped catalog**

`get_catalog()` is lazy, so a broken `catalog.json` would first surface as a 500 on some unlucky request. The spec is explicit that this is a startup failure, not a degradation — serving predictions from a half-loaded marker set corrupts every cache it touches. In `create_app`, inside the `with app.app_context():` block and **before** the blueprint registrations, add:

```python
        # Fail fast: a malformed shipped catalog must not become a 500 on a
        # random later request, and must never produce partial predictions.
        from flymanager.utils.phenotypes.marker_catalog import get_catalog

        get_catalog()
```

Note this deliberately does *not* wrap in try/except, unlike the `preload_*` warm-up calls below it: those are optional caches, this is required reference data.

- [ ] **Step 7: Verify the app still boots and serves**

Run: `python -m pytest tests/test_route.py tests/test_phenotype_routes.py -q -p no:cacheprovider`
Expected: baseline.

- [ ] **Step 8: Commit**

```bash
git add flymanager/utils/phenotypes/marker_catalog.py flymanager/app/__init__.py \
        tests/test_marker_catalog_refresh.py
git commit -m "feat: refresh the marker catalog from Mongo on a throttled revision probe"
```

---

### Task 11: Stamp the catalog signature on both cache envelopes

**Files:**
- Modify: `flymanager/utils/phenotypes/predictor.py:11,415-422,425-455,503-524,526-550`
- Modify: `flymanager/app/services/stock_standardization.py:492,530-560,562-578`
- Test: `tests/test_marker_catalog_signature_plumbing.py`

**Interfaces:**
- Consumes: `marker_catalog.get_catalog()["signature"]`.
- Produces: `predictor.marker_catalog_signature() -> str` and `stock_standardization.marker_catalog_signature()` are not needed — both modules call `get_catalog()["signature"]` inline, mirroring how they call `compute_flybase_pipeline_signature()` today. `PHENOTYPE_CACHE_VERSION` goes 2 → 3; `STANDARDIZATION_CACHE_VERSION` goes 1 → 2.

**Why `StandardizationCache` too:** `review_stock_standardization` decides what counts as a flagged token from the marker dictionaries (converted in Task 5), so adding a marker changes reviewer output. A signature on `PhenotypeCache` alone would leave the reviewer serving stale flags.

**Version bump collision:** the approved reviewer-apply spec also bumps `STANDARDIZATION_CACHE_VERSION` to 2. Whichever lands second takes 3. If that spec is already implemented when you get here, bump to 3 instead and say so in the commit message.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_catalog_signature_plumbing.py`:

```python
import pytest

from flymanager.app.services.stock_standardization import (
    STANDARDIZATION_CACHE_VERSION, build_cross_standardization_cache,
    build_stock_standardization_cache, get_cached_cross_standardization,
    get_cached_stock_standardization)
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.predictor import (
    PHENOTYPE_CACHE_VERSION, build_cross_phenotype_cache,
    build_stock_phenotype_cache, get_cached_cross_phenotype,
    get_cached_stock_phenotype)

GENOTYPE = "w[1118]; CyO/Sp"


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _mutate_catalog():
    """Change one marker's effect text, which must move the signature."""
    shipped = marker_catalog.load_shipped_catalog()
    row = next(d for d in shipped["definitions"] if d["Key"] == "Cy")
    marker_catalog.set_catalog(marker_catalog.compile_catalog(
        shipped,
        [dict(row, origin="user",
              payload=dict(row["payload"], effect="edited for the test"))],
    ))


def test_cache_versions_were_bumped():
    assert PHENOTYPE_CACHE_VERSION == 3
    assert STANDARDIZATION_CACHE_VERSION >= 2


def test_stock_phenotype_cache_carries_the_catalog_signature():
    cache = build_stock_phenotype_cache(GENOTYPE)
    assert cache["markerCatalogSignature"] == marker_catalog.get_catalog()["signature"]


def test_cross_phenotype_cache_carries_the_catalog_signature():
    cache = build_cross_phenotype_cache(GENOTYPE, GENOTYPE)
    assert cache["markerCatalogSignature"] == marker_catalog.get_catalog()["signature"]


def test_strict_read_rejects_a_stale_catalog_signature():
    record = {"Genotype": GENOTYPE, "PhenotypeCache": build_stock_phenotype_cache(GENOTYPE)}
    assert get_cached_stock_phenotype(record, strict=True) is not None
    _mutate_catalog()
    assert get_cached_stock_phenotype(record, strict=True) is None


def test_non_strict_read_still_serves_a_stale_catalog_signature():
    record = {"Genotype": GENOTYPE, "PhenotypeCache": build_stock_phenotype_cache(GENOTYPE)}
    _mutate_catalog()
    assert get_cached_stock_phenotype(record, strict=False) is not None


def test_strict_cross_read_rejects_a_stale_catalog_signature():
    record = {
        "MaleGenotype": GENOTYPE,
        "FemaleGenotype": GENOTYPE,
        "PhenotypeCache": build_cross_phenotype_cache(GENOTYPE, GENOTYPE),
    }
    assert get_cached_cross_phenotype(record, strict=True) is not None
    _mutate_catalog()
    assert get_cached_cross_phenotype(record, strict=True) is None


def test_a_cache_written_before_this_change_has_no_signature_and_is_tolerated():
    """Absent signature means 'written before signatures existed'; the version
    bump is what forces those to recompute, exactly as pipelineSignature does."""
    cache = build_stock_phenotype_cache(GENOTYPE)
    cache.pop("markerCatalogSignature")
    record = {"Genotype": GENOTYPE, "PhenotypeCache": cache}
    assert get_cached_stock_phenotype(record, strict=True) is not None


def test_standardization_caches_carry_and_check_the_signature():
    stock_record = {"Genotype": GENOTYPE,
                    "StandardizationCache": build_stock_standardization_cache(GENOTYPE)}
    cross_record = {
        "MaleGenotype": GENOTYPE,
        "FemaleGenotype": GENOTYPE,
        "StandardizationCache": build_cross_standardization_cache(GENOTYPE, GENOTYPE),
    }
    assert stock_record["StandardizationCache"]["markerCatalogSignature"] == \
        marker_catalog.get_catalog()["signature"]
    assert get_cached_stock_standardization(stock_record, strict=True) is not None
    assert get_cached_cross_standardization(cross_record, strict=True) is not None

    _mutate_catalog()
    assert get_cached_stock_standardization(stock_record, strict=True) is None
    assert get_cached_cross_standardization(cross_record, strict=True) is None
    assert get_cached_stock_standardization(stock_record, strict=False) is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_catalog_signature_plumbing.py -q -p no:cacheprovider`
Expected: `test_cache_versions_were_bumped` FAILS with `assert 2 == 3`, and the signature tests FAIL with `KeyError: 'markerCatalogSignature'`.

- [ ] **Step 3: Plumb the signature through `predictor.py`**

Add the import:

```python
from flymanager.utils.phenotypes.marker_catalog import get_catalog
```

Bump `PHENOTYPE_CACHE_VERSION = 3`. In both builders, add the field next to `pipelineSignature`:

```python
def build_stock_phenotype_cache(genotype):
    return {
        "version": PHENOTYPE_CACHE_VERSION,
        "computedAt": _cache_timestamp(),
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "markerCatalogSignature": get_catalog()["signature"],
        "genotype": genotype,
        "prediction": predict_stock_phenotype(genotype),
    }
```

and the same line in `build_cross_phenotype_cache`. In both strict checks, add a third clause after the `pipelineSignature` one:

```python
    if strict:
        if cache.get("version") != PHENOTYPE_CACHE_VERSION:
            return None
        if cache.get("pipelineSignature") and str(cache.get("pipelineSignature", "")) != compute_flybase_pipeline_signature():
            return None
        if cache.get("markerCatalogSignature") and str(cache.get("markerCatalogSignature", "")) != get_catalog()["signature"]:
            return None
```

The `and` guard mirrors the existing `pipelineSignature` handling: a missing value means "written before this field existed", which the version bump already forces to recompute. Update the `get_cached_stock_phenotype` docstring to say the strict check now covers both signatures.

- [ ] **Step 4: Plumb it through `stock_standardization.py`**

Add `from flymanager.utils.phenotypes.marker_catalog import get_catalog`, set `STANDARDIZATION_CACHE_VERSION = 2`, add `"markerCatalogSignature": get_catalog()["signature"],` to both builders, and extend the shared staleness check:

```python
def _standardization_cache_is_current(cache, strict):
    if not isinstance(cache, dict):
        return False
    if not strict:
        return True
    if cache.get("version") != STANDARDIZATION_CACHE_VERSION:
        return False
    signature = str(cache.get("pipelineSignature", ""))
    if signature and signature != compute_flybase_pipeline_signature():
        return False
    catalog_signature = str(cache.get("markerCatalogSignature", ""))
    if catalog_signature and catalog_signature != get_catalog()["signature"]:
        return False
    return True
```

- [ ] **Step 5: Refresh the catalog in every background task**

**This step is not optional and the two above are dangerous without it.** The `before_request` hook from Task 10 only runs in the web process. The RQ worker builds its app once (`jobs/__init__.py:46-58`) and its snapshot is whatever `create_app` compiled — shipped-only, with no overlay. Three existing worker tasks write phenotype/standardization caches: `task_backfill_phenotype_cache` (`jobs/tasks.py:156`), `_rebuild_caches_after_flybase_refresh` (`:231`) and `task_force_recompute_cache` (`:302`). Once Step 3 makes those caches carry and compare the catalog signature, a worker with a stale snapshot recomputes records **with the wrong marker set** and stamps a signature the web process then rejects — the two processes ping-pong, rebuilding the collection against each other.

Fix it once, in the shared task envelope `_run` (`jobs/tasks.py:21-35`), so it cannot be forgotten by a future task:

```python
def _run(key, work):
    """Shared mark-running/succeeded/failed envelope for every task below."""
    app = get_worker_app()
    with app.app_context():
        from flymanager.app import db
        from flymanager.utils.phenotypes.marker_catalog import refresh_catalog

        # The worker has no before_request hook, so its marker catalog would
        # otherwise stay at whatever create_app compiled -- shipped-only, with
        # no overlay. Any task that writes a materialized cache must not run
        # against a stale marker set.
        try:
            refresh_catalog(db)
        except Exception:
            app.logger.exception("Unable to refresh the marker catalog before job %s", key)

        mark_job_running(db, key)
```

Then add a test to `tests/test_marker_catalog_signature_plumbing.py`:

```python
def test_the_worker_envelope_refreshes_the_catalog_before_running(monkeypatch):
    """A worker that skipped this would recompute every cache against the
    shipped-only marker set and stamp a signature the web process rejects."""
    from flymanager.app.jobs import tasks

    calls = []
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.marker_catalog.refresh_catalog",
        lambda db, **kwargs: calls.append("refreshed"))
    monkeypatch.setattr(tasks, "mark_job_running", lambda *a, **k: calls.append("running"))
    monkeypatch.setattr(tasks, "mark_job_succeeded", lambda *a, **k: None)

    tasks._run("job-key", lambda app, db: {"ok": True})

    assert calls[0] == "refreshed", "the refresh must happen before the job body"
```

`task_rebuild_marker_caches` (Task 13) keeps its own `force=True` refresh: it must see the specific edit that triggered it, not merely a revision-current snapshot.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_marker_catalog_signature_plumbing.py tests/test_background_jobs.py tests/test_phenotype_cache_persistence.py tests/test_standardization_cache.py tests/test_phenotype_backfill.py -q -p no:cacheprovider`
Expected: the new file PASSES (9 tests). The other files may have tests asserting `PHENOTYPE_CACHE_VERSION == 2` or a literal envelope — update those assertions to the new version; that is an intended consequence of the bump, not a regression. Any other failure must match baseline.

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/phenotypes/predictor.py \
        flymanager/app/services/stock_standardization.py \
        flymanager/app/jobs/tasks.py \
        tests/test_marker_catalog_signature_plumbing.py
git commit -m "feat: stamp and check the marker catalog signature on both cache envelopes"
```

---

### Task 12: Affected-token derivation with reverse-reference closure

**Files:**
- Create: `flymanager/utils/phenotypes/marker_rebuild.py`
- Test: `tests/test_marker_rebuild_scope.py`

**Interfaces:**
- Consumes: `marker_catalog.get_catalog()` indexes `definitions`, `balancers_referencing`, `balancers`, `aliases_by_target`, `construct_markers`.
- Produces:
  - `derive_affected_tokens(snapshot, keys, *, previous_documents=(), deleted_override_keys=()) -> set[str] | None` — `None` means "not scopable, rebuild everything".
  - `build_affected_query(tokens, *, genotype_fields) -> dict | None` — a `$or` of case-insensitive `$regex` clauses over the given fields.

**The reverse-reference problem:** balancers resolve their markers by key reference (`resolver.py`, the `default_markers` loop). Editing `Cy` therefore changes every record containing `CyO`, `SM1`, `SM5`, `SM6a` or `SM6b`, none of which contains the literal string `Cy` as a separate token. Missing that closure means silently stale predictions — the exact failure the signature exists to catch, defeated by the targeting.

**Deliberate direction of error:** matching is substring, not token-aware. Editing `B` sweeps most of the collection. Extra rebuilds are wasted work; missed rebuilds are wrong data.

**Two ways this silently under-computes, both of which must be handled here** — because Task 13 then *stamps* whatever this misses, making the staleness permanent rather than self-correcting:

1. **Removals are invisible in the post-edit snapshot.** Editing balancer `Binsc` to drop its alias `Binsn` yields the affected set `{Binsc}`. Records whose genotype says `Binsn` — recognised by the parser before the edit, unrecognised after — are never swept. The derivation therefore also takes the **pre-edit documents** and unions their own identifying tokens (`Key`, `match.symbol`, `match.aliases`, `match.token`, `payload.value`) into the set. Reverse references do not need the old snapshot: a change to a balancer's `default_markers` only affects records containing that balancer, which its own symbol already sweeps.
2. **Genotypes reach a marker through case-insensitive alias resolution.** `resolver.py` resolves leftover tokens via `lookup_flybase_marker_alias`, whose index is lowercase-normalised (`flybase_pipeline.py`), then hydrates the marker from the catalog. A genotype containing `sco` resolves to `sna[Sco]`, so editing that allele row must sweep `sco` too. The query is therefore built with `$options: "i"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_rebuild_scope.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.marker_rebuild import (build_affected_query,
                                                        derive_affected_tokens)


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _snapshot():
    return marker_catalog.get_catalog()


def test_editing_a_balancer_marker_closes_over_every_referencing_balancer():
    tokens = derive_affected_tokens(_snapshot(), ["Cy"])
    assert {"Cy", "CyO", "SM1", "SM5", "SM6a", "SM6b"} <= tokens


def test_the_closure_includes_balancer_aliases():
    tokens = derive_affected_tokens(_snapshot(), ["sc"])
    assert "Binsc" in tokens
    assert "Binsn" in tokens, "an alias of a referencing balancer must be swept too"


def test_editing_an_allele_marker_includes_aliases_pointing_at_it():
    tokens = derive_affected_tokens(_snapshot(), ["wg[Gla-1]"])
    assert "wg[Gla-1]" in tokens
    assert "Gla" in tokens


def test_editing_an_alias_includes_its_target():
    tokens = derive_affected_tokens(_snapshot(), ["Gla"])
    assert {"Gla", "wg[Gla-1]"} <= tokens


def test_editing_a_balancer_includes_its_symbol_and_aliases_but_not_its_markers():
    tokens = derive_affected_tokens(_snapshot(), ["Binsc"])
    assert {"Binsc", "Binsn"} <= tokens
    assert "sc" not in tokens, "a balancer edit does not change the sc marker itself"


def test_an_unknown_key_still_yields_its_own_token():
    assert derive_affected_tokens(_snapshot(), ["nosuchmarker"]) == {"nosuchmarker"}


def test_a_removed_balancer_alias_is_swept_via_the_previous_document():
    """Post-edit the alias is gone, so only the pre-edit document knows a
    genotype saying 'Binsn' is now affected."""
    previous = {"Key": "Binsc", "kind": "balancer",
                "match": {"symbol": "Binsc", "aliases": ["Binsn"]}}
    tokens = derive_affected_tokens(_snapshot(), ["Binsc"], previous_documents=[previous])
    assert {"Binsc", "Binsn"} <= tokens


def test_a_removed_alias_row_is_swept_via_its_previous_target():
    previous = {"Key": "Gla", "kind": "alias", "match": {"token": "Gla"},
                "payload": {"alias_type": "allele_token", "value": "wg[Gla-1]"}}
    tokens = derive_affected_tokens(_snapshot(), ["Gla"], previous_documents=[previous])
    assert {"Gla", "wg[Gla-1]"} <= tokens


def test_a_construct_marker_edit_is_not_scopable():
    assert derive_affected_tokens(_snapshot(), ["construct:w+"]) is None


def test_deleting_a_shipped_override_is_not_scopable():
    assert derive_affected_tokens(_snapshot(), ["Cy"], deleted_override_keys=["Cy"]) is None


def test_no_keys_yields_an_empty_scope_not_a_full_rebuild():
    assert derive_affected_tokens(_snapshot(), []) == set()


def test_build_affected_query_escapes_regex_metacharacters():
    query = build_affected_query({"wg[Gla-1]"}, genotype_fields=("Genotype",))
    pattern = query["$or"][0]["Genotype"]["$regex"]
    assert r"\[" in pattern and r"\]" in pattern


def test_build_affected_query_is_case_insensitive():
    """Genotypes reach markers through the lowercase-normalised FlyBase alias
    index, so 'sco' must match a sweep of 'Sco'."""
    query = build_affected_query({"Sco"}, genotype_fields=("Genotype",))
    assert query["$or"][0]["Genotype"]["$options"] == "i"


def test_build_affected_query_covers_every_field():
    query = build_affected_query({"Cy"}, genotype_fields=("MaleGenotype", "FemaleGenotype"))
    assert {clause_field for clause in query["$or"] for clause_field in clause} == \
        {"MaleGenotype", "FemaleGenotype"}


def test_build_affected_query_with_no_tokens_matches_nothing():
    assert build_affected_query(set(), genotype_fields=("Genotype",)) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_rebuild_scope.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'flymanager.utils.phenotypes.marker_rebuild'`

- [ ] **Step 3: Write the module**

Create `flymanager/utils/phenotypes/marker_rebuild.py`:

```python
"""Scoping and execution of materialized-cache rebuilds after a marker edit.

Any marker write moves the catalog signature, which makes every stored
prediction stale. Recomputing the whole collection for a one-line text tweak
is not acceptable, so a write rebuilds only the records whose genotype can
actually be affected, then stamps the new signature onto the rest.
"""
import re

UNSCOPABLE_KINDS = ("construct_marker",)


def _own_tokens(document):
    """Tokens by which a definition can appear in a genotype, from the
    document alone -- no snapshot needed, so this also works for a pre-edit
    document whose values no longer exist in the current catalog."""
    match = (document or {}).get("match") or {}
    payload = (document or {}).get("payload") or {}
    tokens = {str(document.get("Key") or ""), str(match.get("symbol") or ""),
              str(match.get("token") or ""), str(payload.get("value") or "")}
    tokens.update(str(alias) for alias in (match.get("aliases") or []))
    return {token for token in tokens if token}


def derive_affected_tokens(snapshot, keys, *, previous_documents=(), deleted_override_keys=()):
    """Genotype substrings whose records must be recomputed for these key edits.

    Returns None when the change cannot be scoped and the caller must rebuild
    everything. Two cases:

    * ``construct_marker`` edits, because the match is a pattern *inside*
      construct bodies (``P{...}w[+mC]``), not a whole token we can search for.
    * Deleting an override of a shipped key, because the restored shipped
      definition's blast radius is not derivable from the deleted row alone.
    """
    keys = [str(key or "").strip() for key in keys or []]
    keys = [key for key in keys if key]
    if deleted_override_keys:
        return None

    definitions = snapshot.get("definitions") or {}
    for key in keys:
        definition = definitions.get(key)
        if definition is not None and definition.get("kind") in UNSCOPABLE_KINDS:
            return None
        if key in (snapshot.get("construct_markers") or {}):
            return None

    tokens = set()
    # Pre-edit documents first: a removed alias or a renamed symbol exists
    # nowhere in the post-edit snapshot, so only the old document can tell us
    # which genotypes just stopped resolving the way they used to.
    for document in previous_documents or []:
        if (document or {}).get("kind") in UNSCOPABLE_KINDS:
            return None
        tokens.update(_own_tokens(document))

    balancers = snapshot.get("balancers") or {}
    balancers_referencing = snapshot.get("balancers_referencing") or {}
    aliases_by_target = snapshot.get("aliases_by_target") or {}

    for key in keys:
        tokens.add(key)

        # Aliases that resolve *to* this key, and the target this key
        # resolves to, both change what a genotype containing either means.
        tokens.update(aliases_by_target.get(key, set()))
        definition = definitions.get(key)
        if definition is not None and definition.get("kind") == "alias":
            target = str((definition.get("payload") or {}).get("value") or "").strip()
            if target:
                tokens.add(target)

        # Reverse references: a balancer resolves its markers by key, so
        # editing a marker changes every balancer that lists it -- and those
        # balancers' aliases, which are what actually appear in genotypes.
        for symbol in balancers_referencing.get(key, set()):
            tokens.add(symbol)
            tokens.update((balancers.get(symbol) or {}).get("aliases") or [])

        # Editing a balancer sweeps its own aliases, but not its markers:
        # the markers themselves did not change.
        if definition is not None and definition.get("kind") == "balancer":
            symbol = str((definition.get("match") or {}).get("symbol") or key)
            tokens.add(symbol)
            tokens.update((balancers.get(symbol) or {}).get("aliases") or [])

    return tokens


def build_affected_query(tokens, *, genotype_fields):
    """A Mongo query matching records whose genotype contains any token.

    Substring matching over-matches on purpose: editing ``B`` sweeps most of
    the collection. Extra rebuilds cost time; missed rebuilds are silently
    wrong predictions. Returns None when there is nothing to match.
    """
    tokens = sorted(token for token in (tokens or set()) if token)
    if not tokens:
        return None

    pattern = "|".join(re.escape(token) for token in tokens)
    # Case-insensitive on purpose: the resolver reaches markers through the
    # lowercase-normalised FlyBase alias index, so a genotype spelling of
    # "sco" resolves to sna[Sco] and must be swept when that row is edited.
    return {"$or": [{field: {"$regex": pattern, "$options": "i"}}
                    for field in genotype_fields]}
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_marker_rebuild_scope.py -q -p no:cacheprovider`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/phenotypes/marker_rebuild.py tests/test_marker_rebuild_scope.py
git commit -m "feat: derive rebuild scope from marker edits with reverse-reference closure"
```

---

### Task 13: Targeted rebuild and signature stamping

**Files:**
- Modify: `flymanager/utils/phenotypes/marker_rebuild.py` (add the rebuild driver)
- Modify: `tests/mongo_fakes.py` (dotted-path `$set`; `$and` combined with `$or`)
- Modify: `flymanager/app/jobs/tasks.py` (add `task_rebuild_marker_caches`)
- Test: `tests/test_marker_rebuild_execution.py`

**Interfaces:**
- Consumes: `derive_affected_tokens`, `build_affected_query` (Task 12); `backfill_stock_phenotype_cache` / `backfill_cross_phenotype_cache` (`flymanager/utils/phenotypes/backfill.py`, both accept a `query=` passthrough via `backfill_materialized_cache`); `backfill_stock_standardization_cache` / `backfill_cross_standardization_cache`; `get_cached_*` staleness predicates; `marker_catalog.get_catalog()["signature"]`.
- Produces:
  - `rebuild_after_marker_change(db, keys, *, previous_documents=(), deleted_override_keys=()) -> dict` with keys `scope` (`"targeted"`/`"full"`), `tokens`, and per-collection `{rebuilt, stamped}` counts.
  - `stamp_current_caches(collection, *, cache_field, cache_getter, signature, query=None) -> int`.
  - `tasks.task_rebuild_marker_caches(key, username, *, keys, previous_documents=(), deleted_override_keys=())`.

**The stamping risk, stated plainly:** after a targeted rebuild, records that were *not* affected but are otherwise current get the new signature written onto them without recomputation. Without this the next `force=False` backfill rebuilds the whole collection anyway and the targeting buys nothing. The cost is that a bug in `derive_affected_tokens` is permanently hidden instead of self-correcting on the next backfill. Mitigations: the reverse-reference closure test in Task 12, and the existing guarded force-recompute (typed phrase + 24h cooldown, `cache_force_refresh.py`) as the escape hatch.

- [ ] **Step 1: Extend the Mongo fakes**

`FakeCollection.bulk_write` and `update_one` apply `$set` with `record.update(...)`, which writes a literal `"PhenotypeCache.markerCatalogSignature"` key instead of nesting. And `_matches` returns on `$or` immediately, ignoring sibling keys, so `{"$or": [...], "version": 3}` silently ignores `version`. Both are wrong for this task's queries. In `tests/mongo_fakes.py`:

```python
def _apply_set(record, updates):
    """Apply a $set document, honouring dotted paths like "Cache.field"."""
    for field, value in (updates or {}).items():
        if "." not in field:
            record[field] = value
            continue
        head, _, tail = field.partition(".")
        target = record.setdefault(head, {})
        if not isinstance(target, dict):
            target = {}
            record[head] = target
        _apply_set(target, {tail: value})
```

Use `_apply_set(record, update.get("$set", {}))` in `update_one`, `find_one_and_update` and `bulk_write` in place of every `record.update(update.get("$set", {}))`.

`_matches_operator_clause` also raises `NotImplementedError` on any operator it does not know, and Task 12's query pairs `$regex` with `$options: "i"`. Teach it that pair:

```python
        elif operator == "$regex":
            flags = re.IGNORECASE if "i" in str(clause.get("$options", "")) else 0
            if actual is None or not re.search(operand, str(actual), flags):
                return False
        elif operator == "$options":
            continue  # consumed by the $regex branch above
```

And fix `_matches` so `$or`/`$and` combine with sibling keys instead of short-circuiting:

```python
def _matches(record, query):
    query = query or {}
    if "$and" in query and not all(_matches(record, clause) for clause in query["$and"]):
        return False
    if "$or" in query and not any(_matches(record, clause) for clause in query["$or"]):
        return False
    for key, value in query.items():
        if key in ("$or", "$and"):
            continue
        if isinstance(value, dict) and any(k.startswith("$") for k in value):
            if not _matches_operator_clause(record.get(key), value,
                                            field_present=key in record):
                return False
        elif record.get(key) != value:
            return False
    return True
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_marker_rebuild_execution.py`:

```python
import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.marker_rebuild import \
    rebuild_after_marker_change
from flymanager.utils.phenotypes.predictor import build_stock_phenotype_cache
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _stock(unique_id, genotype, *, cache=True, stale_genotype=False):
    record = {"_id": unique_id, "UniqueID": unique_id, "User": "admin",
              "AssignedTo": "", "Genotype": genotype}
    if cache:
        cached_genotype = "something else" if stale_genotype else genotype
        record["PhenotypeCache"] = build_stock_phenotype_cache(cached_genotype)
    return record


def _mutate_catalog_and_bump(db):
    """Simulate the write path: edit a marker, bump the revision, refresh."""
    db["marker_definitions"].insert_one({
        "Key": "Cy",
        "kind": "gene_marker",
        "match": {"symbol": "Cy"},
        "payload": {"body_part": "wing", "effect": "edited curly wings",
                    "dominance": "dominant", "display_label": "Cy",
                    "phenotype_key": "Cy", "chromosome": 2,
                    "scoring_confidence": 0.95},
        "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
        "provenance": {"source": "user"}, "origin": "user",
    })
    db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 1}})
    marker_catalog.refresh_catalog(db, force=True)


def _db(stocks, crosses=()):
    return FakeDatabase({
        "stocks": list(stocks),
        "crosses": list(crosses),
        "marker_definitions": [],
        "settings": [{"markerCatalogRevision": 0}],
    })


def test_only_records_containing_an_affected_token_are_recomputed():
    db = _db([_stock("A", "w[1118]; CyO/Sp"), _stock("B", "w[1118]; +")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["Cy"])

    assert result["scope"] == "targeted"
    signature = marker_catalog.get_catalog()["signature"]
    records = {r["UniqueID"]: r for r in db["stocks"].find({})}
    assert records["A"]["PhenotypeCache"]["markerCatalogSignature"] == signature
    assert "edited curly wings" in str(records["A"]["PhenotypeCache"]["prediction"])
    assert records["B"]["PhenotypeCache"]["markerCatalogSignature"] == signature
    assert "edited curly wings" not in str(records["B"]["PhenotypeCache"]["prediction"])


def test_a_balancer_referencing_the_edited_marker_is_swept():
    db = _db([_stock("A", "w[1118]; SM6a/Sp")])
    _mutate_catalog_and_bump(db)

    rebuild_after_marker_change(db, ["Cy"])

    cache = db["stocks"].find_one({"UniqueID": "A"})["PhenotypeCache"]
    assert "edited curly wings" in str(cache["prediction"])


def test_records_stale_for_other_reasons_are_not_stamped():
    db = _db([_stock("C", "w[1118]; +", stale_genotype=True)])
    _mutate_catalog_and_bump(db)

    rebuild_after_marker_change(db, ["Cy"])

    cache = db["stocks"].find_one({"UniqueID": "C"})["PhenotypeCache"]
    assert cache["markerCatalogSignature"] != marker_catalog.get_catalog()["signature"]


def test_records_with_no_cache_are_left_alone_by_stamping():
    db = _db([_stock("D", "w[1118]; +", cache=False)])
    _mutate_catalog_and_bump(db)

    rebuild_after_marker_change(db, ["Cy"])

    assert "PhenotypeCache" not in db["stocks"].find_one({"UniqueID": "D"})


def test_a_construct_marker_edit_rebuilds_everything():
    db = _db([_stock("A", "w[1118]; CyO/Sp"), _stock("B", "w[1118]; +")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["construct:w+"])

    assert result["scope"] == "full"
    assert result["stocks"]["rebuilt"] == 2
    # Nothing left to stamp: the rebuild already wrote the new signature onto
    # every record, and stamping skips caches that already carry it.
    assert result["stocks"]["stamped"] == 0


def test_a_removed_balancer_alias_is_swept_when_the_prior_document_is_given():
    db = _db([_stock("A", "w[1118]; Binsn/Y")])
    _mutate_catalog_and_bump(db)
    previous = {"Key": "Binsc", "kind": "balancer",
                "match": {"symbol": "Binsc", "aliases": ["Binsn"]}}

    result = rebuild_after_marker_change(db, ["Binsc"], previous_documents=[previous])

    assert "Binsn" in result["tokens"]
    assert result["stocks"]["rebuilt"] == 1


def test_deleting_a_shipped_override_rebuilds_everything():
    db = _db([_stock("A", "w[1118]; CyO/Sp")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["Cy"], deleted_override_keys=["Cy"])

    assert result["scope"] == "full"


def test_crosses_are_swept_on_both_genotype_fields():
    db = _db([], crosses=[{
        "_id": "X", "UniqueID": "X", "User": "admin", "AssignedTo": "",
        "MaleGenotype": "w[1118]; +", "FemaleGenotype": "w[1118]; CyO/Sp",
    }])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, ["Cy"])

    assert result["crosses"]["rebuilt"] == 1
    cache = db["crosses"].find_one({"UniqueID": "X"})["PhenotypeCache"]
    assert cache["markerCatalogSignature"] == marker_catalog.get_catalog()["signature"]


def test_an_empty_scope_stamps_without_rebuilding_anything():
    db = _db([_stock("A", "w[1118]; CyO/Sp")])
    _mutate_catalog_and_bump(db)

    result = rebuild_after_marker_change(db, [])

    assert result["scope"] == "targeted"
    assert result["stocks"]["rebuilt"] == 0
    assert result["stocks"]["stamped"] == 1
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_rebuild_execution.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'rebuild_after_marker_change'`

- [ ] **Step 4: Write the rebuild driver**

Append to `flymanager/utils/phenotypes/marker_rebuild.py` (move `from pymongo import UpdateOne` up to sit beside the existing `import re` rather than leaving it mid-file):

```python
from pymongo import UpdateOne

STOCK_GENOTYPE_FIELDS = ("Genotype",)
CROSS_GENOTYPE_FIELDS = ("MaleGenotype", "FemaleGenotype")


def stamp_current_caches(collection, *, cache_field, cache_getter, signature, query=None):
    """Write the new catalog signature onto caches that are otherwise current.

    A record is stamped only when ``cache_getter(record, strict=True)`` still
    accepts it once the signature clause is ignored -- i.e. it is current on
    version, pipeline signature and genotype. Records with no cache, or stale
    for any other reason, are left for the next backfill.

    This is what makes targeting worthwhile: without it the next force=False
    backfill would recompute the whole collection anyway. The tradeoff is that
    a bug in derive_affected_tokens becomes permanently invisible here rather
    than self-correcting; the guarded force-recompute is the escape hatch.
    """
    operations = []
    for record in collection.find(query or {}):
        cache = record.get(cache_field)
        if not isinstance(cache, dict):
            continue
        if cache.get("markerCatalogSignature") == signature:
            # Already current -- typically a record the rebuild just wrote.
            continue
        probe = dict(record)
        probe[cache_field] = dict(cache, markerCatalogSignature=signature)
        if cache_getter(probe, strict=True) is None:
            continue
        operations.append(UpdateOne(
            {"_id": record["_id"]},
            {"$set": {f"{cache_field}.markerCatalogSignature": signature}},
        ))

    if operations:
        collection.bulk_write(operations, ordered=False)
    return len(operations)


def rebuild_after_marker_change(db, keys, *, previous_documents=(), deleted_override_keys=()):
    """Recompute the caches a marker edit can affect, then stamp the rest.

    Runs after the catalog snapshot has already been refreshed, so
    get_catalog()["signature"] is the post-edit signature.
    """
    from flymanager.app.services.standardization_backfill import (
        backfill_cross_standardization_cache,
        backfill_stock_standardization_cache)
    from flymanager.app.services.stock_standardization import (
        get_cached_cross_standardization, get_cached_stock_standardization)
    from flymanager.utils.phenotypes.backfill import (
        backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)
    from flymanager.utils.phenotypes.marker_catalog import get_catalog
    from flymanager.utils.phenotypes.predictor import (
        get_cached_cross_phenotype, get_cached_stock_phenotype)

    snapshot = get_catalog()
    signature = snapshot["signature"]
    tokens = derive_affected_tokens(snapshot, keys,
                                    previous_documents=previous_documents,
                                    deleted_override_keys=deleted_override_keys)

    result = {
        "scope": "full" if tokens is None else "targeted",
        "tokens": sorted(tokens) if tokens is not None else None,
    }

    plans = (
        ("stocks", STOCK_GENOTYPE_FIELDS,
         backfill_stock_phenotype_cache, get_cached_stock_phenotype,
         backfill_stock_standardization_cache, get_cached_stock_standardization),
        ("crosses", CROSS_GENOTYPE_FIELDS,
         backfill_cross_phenotype_cache, get_cached_cross_phenotype,
         backfill_cross_standardization_cache, get_cached_cross_standardization),
    )

    for (name, fields, phenotype_backfill, phenotype_getter,
         standardization_backfill, standardization_getter) in plans:
        collection = db[name]
        query = None if tokens is None else build_affected_query(tokens, genotype_fields=fields)

        rebuilt = 0
        if tokens is None or query is not None:
            phenotype_summary = phenotype_backfill(collection, query=query, force=True)
            standardization_backfill(collection, query=query, force=True)
            rebuilt = phenotype_summary["updated"]

        stamped = stamp_current_caches(
            collection, cache_field="PhenotypeCache",
            cache_getter=phenotype_getter, signature=signature,
        )
        stamp_current_caches(
            collection, cache_field="StandardizationCache",
            cache_getter=standardization_getter, signature=signature,
        )
        result[name] = {"rebuilt": rebuilt, "stamped": stamped}

    return result
```

The backfill wrappers must accept and forward `query`. Check `flymanager/utils/phenotypes/backfill.py` and `flymanager/app/services/standardization_backfill.py`: `backfill_materialized_cache` already takes `query=None`, but the four wrappers do not expose it. Add `query=None` to each wrapper signature and pass it through. When `query` is None the wrapper must keep building the user query from `users`, exactly as today.

Note the ordering: rebuild first (which writes fresh caches carrying the new signature), then stamp — so freshly rebuilt records are simply already correct and the stamp is a no-op write for them.

- [ ] **Step 5: Add the background task**

In `flymanager/app/jobs/tasks.py`:

```python
def task_rebuild_marker_caches(key, username, *, keys, previous_documents=(),
                               deleted_override_keys=()):
    def work(app, db):
        from flymanager.utils.phenotypes.marker_catalog import refresh_catalog
        from flymanager.utils.phenotypes.marker_rebuild import \
            rebuild_after_marker_change

        # force=True rather than waiting out the before_request interval: the
        # worker must see the edit that triggered this job.
        refresh_catalog(db, force=True)
        summary = rebuild_after_marker_change(
            db, keys, previous_documents=previous_documents,
            deleted_override_keys=deleted_override_keys)
        write_activity(username, f"Rebuilt caches after marker change: {', '.join(keys) or 'catalog'}", db)
        return {
            "message": (
                f"Marker cache rebuild ({summary['scope']}) complete: "
                f"{summary['stocks']['rebuilt']} stocks and "
                f"{summary['crosses']['rebuilt']} crosses recomputed, "
                f"{summary['stocks']['stamped'] + summary['crosses']['stamped']} stamped."
            ),
            "summary": summary,
        }

    _run(key, work)
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_marker_rebuild_execution.py tests/test_mongo_fakes.py tests/test_bulk_operations.py tests/test_phenotype_backfill.py tests/test_materialized_cache_errors.py -q -p no:cacheprovider`
Expected: the new file PASSES (9 tests); the fakes-dependent files at baseline — the `_matches` and `$set` fixes are strictly more correct, so any new failure there is a test that was relying on the bug and needs its expectation examined, not the fix reverted.

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/phenotypes/marker_rebuild.py flymanager/utils/phenotypes/backfill.py \
        flymanager/app/services/standardization_backfill.py flymanager/app/jobs/tasks.py \
        tests/mongo_fakes.py tests/test_marker_rebuild_execution.py
git commit -m "feat: rebuild affected caches and stamp the rest after a marker change"
```

---

### Task 14: Definition CRUD, ownership, and promotion

**Files:**
- Create: `flymanager/utils/mongo/marker_definitions.py`
- Modify: `flymanager/utils/mongo/db.py:51-77` (index), `flymanager/utils/mongo/__init__.py` (exports)
- Test: `tests/test_marker_definitions_store.py`

**Interfaces:**
- Consumes: `get_settings`/`update_settings`, `write_activity`, `validate_definition` (Task 1), `load_shipped_catalog`, `refresh_catalog`.
- Produces:
  - `MarkerDefinitionError(Exception)` with `.status_code`
  - `list_marker_definitions(db, *, kind=None, origin=None, search=None) -> list[dict]` — the merged shipped+overlay view, each row carrying `origin` and `editable_by`
  - `get_marker_definition(db, key) -> dict | None`
  - `create_marker_definition(db, document, *, username) -> dict`
  - `update_marker_definition(db, key, document, *, username) -> dict`
  - `delete_marker_definition(db, key, *, username) -> dict` with `{"restored_shipped": bool}`
  - `promote_marker_definition(db, key, *, username) -> dict`
  - `bump_marker_catalog_revision(db) -> int`
  - `can_edit_marker_definition(document, username) -> bool`

**Permission rules (spec Part 6):** any logged-in user creates. Edit and delete: the creator, or admin. Once `origin == "curated"`: admin only. Promotion: admin only. `admin_required` is `session["username"] == "admin"` — a single account, not a role flag — so `username == "admin"` is the correct admin test here.

**Visibility:** a user-created definition is live for everyone on the next refresh. Promotion changes trust labelling and edit permission only; there is no behavioural change, because user markers were already live. Attribution is therefore the real safety mechanism: every write records `CreatedBy`/`UpdatedBy` and calls `write_activity`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_marker_definitions_store.py`:

```python
import pytest

from flymanager.utils.mongo.marker_definitions import (
    MarkerDefinitionError, bump_marker_catalog_revision,
    can_edit_marker_definition, create_marker_definition,
    delete_marker_definition, get_marker_definition, list_marker_definitions,
    promote_marker_definition, update_marker_definition)
from flymanager.utils.phenotypes import marker_catalog
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


@pytest.fixture
def db():
    return FakeDatabase({"marker_definitions": [], "settings": [{}], "activity": []})


def _definition(key="zz", **overrides):
    document = {
        "Key": key,
        "kind": "gene_marker",
        "match": {"symbol": key},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": key,
                    "phenotype_key": key, "chromosome": 3,
                    "scoring_confidence": 0.8},
        "provenance": {"source": "user"},
    }
    document.update(overrides)
    return document


def test_create_stores_attribution_and_bumps_the_revision(db):
    created = create_marker_definition(db, _definition(), username="alice")
    assert created["origin"] == "user"
    assert created["CreatedBy"] == "alice"
    assert created["CreatedAt"]
    assert marker_catalog.read_catalog_revision(db) == 1
    assert db["activity"].count_documents({}) == 1


def test_create_rejects_a_duplicate_key_with_409(db):
    create_marker_definition(db, _definition(), username="alice")
    with pytest.raises(MarkerDefinitionError) as exc:
        create_marker_definition(db, _definition(), username="bob")
    assert exc.value.status_code == 409


def test_create_rejects_an_invalid_document_with_400(db):
    with pytest.raises(MarkerDefinitionError) as exc:
        create_marker_definition(db, {"Key": "x", "kind": "nope"}, username="alice")
    assert exc.value.status_code == 400


def test_creating_a_key_that_exists_in_the_shipped_catalog_records_an_override(db):
    created = create_marker_definition(db, _definition("Cy"), username="alice")
    assert created["overridesShipped"]["key"] == "Cy"
    assert created["overridesShipped"]["shippedVersion"] == \
        marker_catalog.load_shipped_catalog()["catalogVersion"]


def test_update_by_the_creator_succeeds_and_bumps_the_revision(db):
    create_marker_definition(db, _definition(), username="alice")
    updated = update_marker_definition(
        db, "zz", _definition(payload={"display_label": "zz", "effect": "edited"}),
        username="alice")
    assert updated["payload"]["effect"] == "edited"
    assert updated["UpdatedBy"] == "alice"
    assert marker_catalog.read_catalog_revision(db) == 2


def test_update_by_another_user_is_forbidden(db):
    create_marker_definition(db, _definition(), username="alice")
    with pytest.raises(MarkerDefinitionError) as exc:
        update_marker_definition(db, "zz", _definition(), username="bob")
    assert exc.value.status_code == 403


def test_admin_can_update_anyones_definition(db):
    create_marker_definition(db, _definition(), username="alice")
    assert update_marker_definition(db, "zz", _definition(), username="admin")


def test_a_curated_definition_is_admin_only(db):
    create_marker_definition(db, _definition(), username="alice")
    promote_marker_definition(db, "zz", username="admin")
    with pytest.raises(MarkerDefinitionError) as exc:
        update_marker_definition(db, "zz", _definition(), username="alice")
    assert exc.value.status_code == 403
    assert update_marker_definition(db, "zz", _definition(), username="admin")


def test_promotion_requires_admin_and_records_the_curator(db):
    create_marker_definition(db, _definition(), username="alice")
    with pytest.raises(MarkerDefinitionError) as exc:
        promote_marker_definition(db, "zz", username="alice")
    assert exc.value.status_code == 403

    promoted = promote_marker_definition(db, "zz", username="admin")
    assert promoted["origin"] == "curated"
    assert promoted["CuratedBy"] == "admin"
    assert promoted["CuratedAt"]


def test_delete_removes_a_user_definition(db):
    create_marker_definition(db, _definition(), username="alice")
    result = delete_marker_definition(db, "zz", username="alice")
    assert result["restored_shipped"] is False
    assert get_marker_definition(db, "zz") is None


def test_deleting_an_override_restores_the_shipped_definition(db):
    create_marker_definition(db, _definition("Cy"), username="alice")
    result = delete_marker_definition(db, "Cy", username="alice")
    assert result["restored_shipped"] is True
    restored = get_marker_definition(db, "Cy")
    assert restored["origin"] == "shipped"
    assert restored["payload"]["effect"] != "zigzag wings"


def test_delete_of_a_missing_key_is_404(db):
    with pytest.raises(MarkerDefinitionError) as exc:
        delete_marker_definition(db, "nope", username="alice")
    assert exc.value.status_code == 404


def test_a_shipped_definition_cannot_be_edited_or_deleted_directly(db):
    """Both paths go through _require_editable, which reports a shipped key as
    409 ("create an override instead") rather than 404: the key does exist,
    it is just not editable in place."""
    with pytest.raises(MarkerDefinitionError) as exc:
        update_marker_definition(db, "Sb", _definition("Sb"), username="admin")
    assert exc.value.status_code == 409
    with pytest.raises(MarkerDefinitionError) as delete_exc:
        delete_marker_definition(db, "Sb", username="admin")
    assert delete_exc.value.status_code == 409


def test_list_merges_shipped_and_overlay_with_edit_flags(db):
    create_marker_definition(db, _definition(), username="alice")
    rows = list_marker_definitions(db)
    by_key = {row["Key"]: row for row in rows}
    assert by_key["Cy"]["origin"] == "shipped"
    assert by_key["Cy"]["editable_by_user"] is False
    assert by_key["zz"]["origin"] == "user"
    assert by_key["zz"]["editable_by_user"] is True


def test_list_filters_by_kind_origin_and_search(db):
    create_marker_definition(db, _definition(), username="alice")
    assert {row["Key"] for row in list_marker_definitions(db, origin="user")} == {"zz"}
    assert all(row["kind"] == "balancer"
               for row in list_marker_definitions(db, kind="balancer"))
    assert {row["Key"] for row in list_marker_definitions(db, search="zigzag")} == {"zz"}


def test_can_edit_marker_definition_rules():
    user_row = {"origin": "user", "CreatedBy": "alice"}
    curated_row = {"origin": "curated", "CreatedBy": "alice"}
    shipped_row = {"origin": "shipped"}
    assert can_edit_marker_definition(user_row, "alice")
    assert can_edit_marker_definition(user_row, "admin")
    assert not can_edit_marker_definition(user_row, "bob")
    assert not can_edit_marker_definition(curated_row, "alice")
    assert can_edit_marker_definition(curated_row, "admin")
    assert not can_edit_marker_definition(shipped_row, "admin")


def test_bump_is_monotonic(db):
    assert bump_marker_catalog_revision(db) == 1
    assert bump_marker_catalog_revision(db) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_definitions_store.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'flymanager.utils.mongo.marker_definitions'`

- [ ] **Step 3: Write the store module**

Create `flymanager/utils/mongo/marker_definitions.py`:

```python
"""CRUD for the marker_definitions overlay collection.

Mongo holds deltas only: user-created definitions and explicit overrides of a
shipped key. The shipped layer (data/markers/catalog.json) is never written
here. Every write bumps markerCatalogRevision, which is what makes other
worker processes converge on the change.
"""
from copy import deepcopy
from datetime import datetime, timezone

from flymanager.utils.mongo.activity import write_activity
from flymanager.utils.mongo.settings import get_settings, update_settings
from flymanager.utils.phenotypes.marker_catalog import (
    MARKER_CATALOG_REVISION_KEY, MARKER_DEFINITIONS_COLLECTION,
    load_shipped_catalog, validate_definition)

ADMIN_USERNAME = "admin"

MUTABLE_FIELDS = ("kind", "match", "payload", "sorting", "audit", "imaging",
                  "expression", "provenance")


class MarkerDefinitionError(Exception):
    """A marker definition write that a route should turn into an HTTP status."""

    def __init__(self, message, *, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _shipped_definitions():
    catalog = load_shipped_catalog()
    return ({definition["Key"]: definition for definition in catalog["definitions"]},
            catalog["catalogVersion"])


def bump_marker_catalog_revision(db):
    revision = int((get_settings(db) or {}).get(MARKER_CATALOG_REVISION_KEY) or 0) + 1
    update_settings({MARKER_CATALOG_REVISION_KEY: revision}, db)
    return revision


def can_edit_marker_definition(document, username):
    origin = (document or {}).get("origin")
    if origin == "shipped":
        return False
    if username == ADMIN_USERNAME:
        return True
    if origin == "curated":
        return False
    return str((document or {}).get("CreatedBy") or "") == str(username or "")


def get_marker_definition(db, key):
    """The effective definition for a key: overlay if present, else shipped."""
    key = str(key or "").strip()
    overlay = db[MARKER_DEFINITIONS_COLLECTION].find_one({"Key": key})
    if overlay is not None:
        return overlay
    shipped, _ = _shipped_definitions()
    return deepcopy(shipped.get(key))


def _overlay_document(db, key):
    return db[MARKER_DEFINITIONS_COLLECTION].find_one({"Key": str(key or "").strip()})


def _normalized(document):
    normalized = {field: deepcopy(document.get(field))
                  for field in MUTABLE_FIELDS if field in document}
    normalized.setdefault("sorting", {})
    normalized.setdefault("audit", {})
    normalized.setdefault("imaging", {"aliases": [], "images": []})
    normalized.setdefault("expression", {})
    normalized.setdefault("provenance", {})
    return normalized


def create_marker_definition(db, document, *, username):
    key = str((document or {}).get("Key") or "").strip()
    if not key:
        raise MarkerDefinitionError("Key is required", status_code=400)

    candidate = dict(_normalized(document), Key=key)
    errors = validate_definition(candidate)
    if errors:
        raise MarkerDefinitionError("; ".join(errors), status_code=400)

    if _overlay_document(db, key) is not None:
        raise MarkerDefinitionError(f"A definition for {key} already exists", status_code=409)

    shipped, shipped_version = _shipped_definitions()
    now = _timestamp()
    candidate.update({
        "origin": "user",
        "CreatedBy": username,
        "CreatedAt": now,
        "UpdatedBy": username,
        "UpdatedAt": now,
    })
    if key in shipped:
        candidate["overridesShipped"] = {"key": key, "shippedVersion": shipped_version}

    db[MARKER_DEFINITIONS_COLLECTION].insert_one(candidate)
    bump_marker_catalog_revision(db)
    write_activity(username, f"Created marker definition {key}", db)
    return candidate


def _require_editable(db, key, username):
    existing = _overlay_document(db, key)
    if existing is None:
        shipped, _ = _shipped_definitions()
        if key in shipped:
            raise MarkerDefinitionError(
                f"{key} is a shipped definition; create an override instead",
                status_code=409)
        raise MarkerDefinitionError(f"No definition for {key}", status_code=404)
    if not can_edit_marker_definition(existing, username):
        raise MarkerDefinitionError(f"You cannot edit {key}", status_code=403)
    return existing


def update_marker_definition(db, key, document, *, username):
    key = str(key or "").strip()
    existing = _require_editable(db, key, username)

    candidate = dict(existing)
    candidate.pop("_id", None)  # never $set the immutable _id on a real Mongo
    candidate.update(_normalized(document))
    candidate["Key"] = key
    errors = validate_definition(candidate)
    if errors:
        raise MarkerDefinitionError("; ".join(errors), status_code=400)

    candidate["UpdatedBy"] = username
    candidate["UpdatedAt"] = _timestamp()

    db[MARKER_DEFINITIONS_COLLECTION].update_one({"Key": key}, {"$set": candidate})
    bump_marker_catalog_revision(db)
    write_activity(username, f"Updated marker definition {key}", db)
    return candidate


def delete_marker_definition(db, key, *, username):
    key = str(key or "").strip()
    existing = _require_editable(db, key, username)
    restored_shipped = bool(existing.get("overridesShipped"))

    db[MARKER_DEFINITIONS_COLLECTION].delete_one({"Key": key})
    bump_marker_catalog_revision(db)
    write_activity(username, f"Deleted marker definition {key}", db)
    return {"key": key, "restored_shipped": restored_shipped}


def promote_marker_definition(db, key, *, username):
    key = str(key or "").strip()
    if username != ADMIN_USERNAME:
        raise MarkerDefinitionError("Admin access required", status_code=403)

    existing = _overlay_document(db, key)
    if existing is None:
        raise MarkerDefinitionError(f"No definition for {key}", status_code=404)

    now = _timestamp()
    updates = {"origin": "curated", "CuratedBy": username, "CuratedAt": now}
    db[MARKER_DEFINITIONS_COLLECTION].update_one({"Key": key}, {"$set": updates})
    bump_marker_catalog_revision(db)
    write_activity(username, f"Promoted marker definition {key} to curated", db)
    return dict(existing, **updates)


def _search_haystack(document):
    return " ".join(str(value) for value in (
        document.get("Key"),
        (document.get("payload") or {}).get("display_label"),
        (document.get("payload") or {}).get("effect"),
        (document.get("payload") or {}).get("body_part"),
    ) if value)


def list_marker_definitions(db, *, kind=None, origin=None, search=None, username=None):
    """The merged shipped+overlay catalog view, newest layer winning by Key."""
    shipped, _ = _shipped_definitions()
    merged = {key: deepcopy(definition) for key, definition in shipped.items()}
    for document in db[MARKER_DEFINITIONS_COLLECTION].find({}):
        merged[str(document.get("Key") or "").strip()] = document

    rows = []
    for key in sorted(merged):
        document = dict(merged[key])
        document.pop("_id", None)
        document["editable_by_user"] = can_edit_marker_definition(document, username)
        if kind and document.get("kind") != kind:
            continue
        if origin and document.get("origin") != origin:
            continue
        if search and search.lower() not in _search_haystack(document).lower():
            continue
        rows.append(document)
    return rows
```

- [ ] **Step 4: Add the unique index and the exports**

In `flymanager/utils/mongo/db.py`, inside `ensure_mongo_indexes`:

```python
    db["marker_definitions"].create_index("Key", unique=True, name="marker_definitions_key")
```

In `flymanager/utils/mongo/__init__.py`, import and re-export the seven public names from `marker_definitions`, following the file's existing grouping-comment and `__all__` conventions.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_marker_definitions_store.py tests/test_db_indexes.py tests/test_mongo_indexes.py -q -p no:cacheprovider`
Expected: the new file PASSES (17 tests); the index tests at baseline. Note `test_mongo_performance_helpers::test_ensure_mongo_indexes_creates_access_indexes` is a known pre-existing failure — leave it.

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/mongo/marker_definitions.py flymanager/utils/mongo/db.py \
        flymanager/utils/mongo/__init__.py tests/test_marker_definitions_store.py
git commit -m "feat: add marker definition CRUD with ownership and promotion"
```

---

### Task 15: The /markers blueprint and UI

**Files:**
- Create: `flymanager/app/routes/markers.py`
- Create: `flymanager/app/templates/markers/list.html`, `flymanager/app/templates/markers/detail.html`
- Modify: `flymanager/app/__init__.py:268-279` (register the blueprint)
- Modify: `flymanager/app/templates/base.html` (nav link)
- Test: `tests/test_marker_routes.py`

**Interfaces:**
- Consumes: the Task 14 store functions; `login_required`/`admin_required` from `routes/auth`; `enqueue_job` + `task_rebuild_marker_caches` (Task 13); `limiter`, `get_json_payload` from `app.security`.
- Produces: routes `GET /markers`, `GET /markers/<key>`, `POST /markers`, `POST /markers/<key>`, `POST /markers/<key>/delete`, `POST /markers/<key>/promote`.

**After every successful write:** refresh this process's catalog (`refresh_catalog(db)`) so the user immediately sees their own change, then enqueue `task_rebuild_marker_caches`. An `OperationLockConflict` from `enqueue_job` is flashed as a warning, matching `settings.py`'s `_enqueue_or_flash_conflict`; the write itself still stands.

- [ ] **Step 1: Write the failing test**

Note the autouse fixture below disables the Task 10 `before_request` hook: it closes over `flymanager.app.db` (the real client), not the blueprint's patched `db`, so left alone it silently recompiles the snapshot from real Mongo mid-request. Patch the name on the `marker_catalog` module, which is what `app/__init__.py` imports inside the hook.

Create `tests/test_marker_routes.py`, following the `_make_app` pattern in `tests/test_phenotype_routes.py` (patch `flymanager.app.get_settings`, set the env vars, `app.config.update(TESTING=True)`), and patching `flymanager.app.routes.markers.db` with a `FakeDatabase`:

```python
from unittest.mock import patch

import pytest

from flymanager.app import create_app
from flymanager.utils.phenotypes import marker_catalog
from tests.mongo_fakes import FakeDatabase


def _settings_payload():
    return {
        "lab_info": {"lab_name": "Test Lab", "admin_name": "Admin",
                     "admin_email": "admin@example.com"},
        "theme": {"accent_color": "#0055aa", "dark_mode": False},
    }


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)
    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        application = create_app()
    application.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return application


@pytest.fixture
def fake_db():
    return FakeDatabase({"marker_definitions": [], "settings": [{}], "activity": []})


@pytest.fixture(autouse=True)
def _reset_catalog(monkeypatch):
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    # The Task 10 before_request hook closes over the real module-global `db`,
    # not the patched blueprint one, so without this it recompiles from the
    # real Mongo and discards whatever the test installed.
    monkeypatch.setattr(marker_catalog, "maybe_refresh_catalog",
                        lambda db, **kwargs: None)
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _client(app, fake_db, username="alice"):
    client = app.test_client()
    with client.session_transaction() as session:
        session["username"] = username
    return client


def _payload(key="zz"):
    return {
        "Key": key,
        "kind": "gene_marker",
        "match": {"symbol": key},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": key,
                    "phenotype_key": key, "chromosome": 3,
                    "scoring_confidence": 0.8},
        "provenance": {"source": "user"},
    }


def test_catalog_requires_login(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = app.test_client().get("/markers")
    assert response.status_code == 302


def test_catalog_lists_shipped_definitions(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers")
    assert response.status_code == 200
    assert b"CyO" in response.data


def test_detail_renders_a_shipped_definition_read_only(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers/Cy")
    assert response.status_code == 200
    assert b"Override" in response.data


def test_detail_404s_for_an_unknown_key(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db):
        assert _client(app, fake_db).get("/markers/nope").status_code == 404


def test_create_enqueues_a_rebuild_and_refreshes_the_catalog(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job") as enqueue:
        response = _client(app, fake_db).post("/markers", json=_payload())
    assert response.status_code == 201
    assert fake_db["marker_definitions"].count_documents({"Key": "zz"}) == 1
    assert enqueue.call_count == 1
    assert enqueue.call_args.kwargs["kwargs"]["keys"] == ["zz"]
    assert marker_catalog.get_catalog()["gene_markers"]["zz"]["effect"] == "zigzag wings"


def test_duplicate_create_returns_409(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        client = _client(app, fake_db)
        client.post("/markers", json=_payload())
        assert client.post("/markers", json=_payload()).status_code == 409


def test_update_by_a_non_creator_returns_403(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        _client(app, fake_db, "alice").post("/markers", json=_payload())
        response = _client(app, fake_db, "bob").post("/markers/zz", json=_payload())
    assert response.status_code == 403


def test_delete_marks_the_deleted_override_for_a_full_rebuild(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job") as enqueue:
        client = _client(app, fake_db)
        client.post("/markers", json=_payload("Cy"))
        response = client.post("/markers/Cy/delete")
    assert response.status_code == 200
    assert enqueue.call_args.kwargs["kwargs"]["deleted_override_keys"] == ["Cy"]


def test_promote_requires_admin(app, fake_db):
    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job"):
        _client(app, fake_db, "alice").post("/markers", json=_payload())
        assert _client(app, fake_db, "alice").post("/markers/zz/promote").status_code == 403
        assert _client(app, fake_db, "admin").post("/markers/zz/promote").status_code == 200


def test_a_queue_conflict_does_not_undo_the_write(app, fake_db):
    from flymanager.utils.mongo.operation_locks import OperationLockConflict

    with patch("flymanager.app.routes.markers.db", fake_db), \
         patch("flymanager.app.routes.markers.enqueue_job",
               side_effect=OperationLockConflict("already running")):
        response = _client(app, fake_db).post("/markers", json=_payload())
    assert response.status_code == 201
    assert fake_db["marker_definitions"].count_documents({"Key": "zz"}) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_marker_routes.py -q -p no:cacheprovider`
Expected: every test 404s or errors on `flymanager.app.routes.markers` not existing.

- [ ] **Step 3: Write the blueprint**

Create `flymanager/app/routes/markers.py`:

```python
"""Marker definition catalog UI and API.

Writes here are the only runtime path that changes marker behaviour. Each one
lands in Mongo, bumps the catalog revision, refreshes this process's snapshot
so the author sees their own change immediately, and enqueues a scoped cache
rebuild for everyone else's stored predictions.
"""
from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from flymanager.app import db
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.routes.auth import login_required
from flymanager.app.security import get_json_payload, limiter
from flymanager.utils.mongo import OperationLockConflict
from flymanager.utils.mongo.marker_definitions import (
    MarkerDefinitionError, create_marker_definition, delete_marker_definition,
    get_marker_definition, list_marker_definitions, promote_marker_definition,
    update_marker_definition)
from flymanager.utils.phenotypes.marker_catalog import MARKER_KINDS, refresh_catalog

bp = Blueprint("markers", __name__)


def _serializable(document):
    """Drop the ObjectId so the document survives RQ's job serialisation."""
    stripped = dict(document or {})
    stripped.pop("_id", None)
    return stripped


def _after_write(keys, *, previous_documents=(), deleted_override_keys=()):
    """Refresh locally, then hand the cache rebuild to the worker.

    A queue conflict means a rebuild is already in flight; the definition
    write itself is already committed and must not be rolled back, so this is
    a warning, not an error.
    """
    refresh_catalog(db, force=True)
    username = session.get("username")
    job_key = f"markers:rebuild:{'-'.join(keys) or 'catalog'}"
    try:
        # Every task in jobs.tasks takes `key` as its first argument, so it is
        # passed through kwargs the same way settings.py does it.
        enqueue_job(
            db,
            key=job_key,
            actor=username,
            label="Marker catalog cache rebuild",
            func=job_tasks.task_rebuild_marker_caches,
            kwargs={"key": job_key, "username": username, "keys": list(keys),
                    "previous_documents": list(previous_documents),
                    "deleted_override_keys": list(deleted_override_keys)},
        )
    except OperationLockConflict as exc:
        flash(str(exc), "warning")


def _error_response(exc):
    if request.is_json:
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    flash(str(exc), "danger")
    return redirect(url_for("markers.marker_catalog")), exc.status_code


@bp.get("/markers")
@login_required
def marker_catalog():
    rows = list_marker_definitions(
        db,
        kind=(request.args.get("kind") or "").strip() or None,
        origin=(request.args.get("origin") or "").strip() or None,
        search=(request.args.get("q") or "").strip() or None,
        username=session.get("username"),
    )
    return render_template("markers/list.html", page_title="Marker Catalog",
                           definitions=rows, kinds=MARKER_KINDS,
                           selected_kind=request.args.get("kind", ""),
                           selected_origin=request.args.get("origin", ""),
                           search_query=request.args.get("q", ""))


@bp.get("/markers/<path:key>")
@login_required
def marker_detail(key):
    definition = get_marker_definition(db, key)
    if definition is None:
        abort(404)
    definition = dict(definition)
    definition.pop("_id", None)
    username = session.get("username")
    from flymanager.utils.mongo.marker_definitions import \
        can_edit_marker_definition
    return render_template(
        "markers/detail.html", page_title=f"Marker: {key}",
        definition=definition, kinds=MARKER_KINDS,
        can_edit=can_edit_marker_definition(definition, username),
        can_promote=(username == "admin" and definition.get("origin") == "user"),
        is_shipped=(definition.get("origin") == "shipped"),
    )


@bp.post("/markers")
@login_required
@limiter.limit("60 per hour")
def create_marker():
    try:
        payload = get_json_payload() if request.is_json else request.form.to_dict()
        created = create_marker_definition(db, payload, username=session.get("username"))
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([created["Key"]])
    if request.is_json:
        return jsonify({"status": "success", "key": created["Key"]}), 201
    # Form path returns a plain 302; a 201 with a Location header is not
    # followed by browsers.
    return redirect(url_for("markers.marker_detail", key=created["Key"]))


@bp.post("/markers/<path:key>")
@login_required
@limiter.limit("60 per hour")
def update_marker(key):
    # Captured before the write: an edit that removes an alias or renames a
    # symbol leaves no trace of the old value in the new snapshot, and the
    # rebuild scope has to sweep genotypes that used the old spelling.
    previous = get_marker_definition(db, key)
    try:
        payload = get_json_payload() if request.is_json else request.form.to_dict()
        update_marker_definition(db, key, payload, username=session.get("username"))
    except ValueError as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([key], previous_documents=[_serializable(previous)] if previous else ())
    if request.is_json:
        return jsonify({"status": "success", "key": key}), 200
    return redirect(url_for("markers.marker_detail", key=key))


@bp.post("/markers/<path:key>/delete")
@login_required
@limiter.limit("60 per hour")
def delete_marker(key):
    previous = get_marker_definition(db, key)
    try:
        result = delete_marker_definition(db, key, username=session.get("username"))
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([key],
                 previous_documents=[_serializable(previous)] if previous else (),
                 deleted_override_keys=[key] if result["restored_shipped"] else ())
    if request.is_json:
        return jsonify({"status": "success", **result}), 200
    return redirect(url_for("markers.marker_catalog"))


@bp.post("/markers/<path:key>/promote")
@login_required
@limiter.limit("60 per hour")
def promote_marker(key):
    try:
        promote_marker_definition(db, key, username=session.get("username"))
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([key])
    if request.is_json:
        return jsonify({"status": "success", "key": key}), 200
    return redirect(url_for("markers.marker_detail", key=key))
```

Register it in `flymanager/app/__init__.py` alongside the others (no `url_prefix`; the routes carry `/markers` themselves, matching how `settings.bp` and `jobs.bp` are registered):

```python
        from flymanager.app.routes import (auth, cross, data, flip, jobs,
                                           main, markers, settings, stock, tray)
        ...
        app.register_blueprint(markers.bp)
```

- [ ] **Step 4: Surface invalid overlay rows**

The spec requires a malformed overlay document to be skipped with a warning **and shown in the UI as invalid**, so one bad row cannot both take the app down and hide. `compile_catalog` already collects them in `snapshot["invalid_definitions"]`. Add to `marker_catalog()` in the blueprint:

```python
    from flymanager.utils.phenotypes.marker_catalog import get_catalog

    invalid_definitions = get_catalog()["invalid_definitions"]
```

pass it to the template, and render it at the top of `list.html` as a warning panel listing each `Key` and its `errors` when the list is non-empty. Add a test:

```python
def test_invalid_overlay_rows_are_surfaced_in_the_catalog(app, fake_db):
    fake_db["marker_definitions"].insert_one({"Key": "", "kind": "gene_marker"})
    fake_db["settings"].update_one({}, {"$set": {"markerCatalogRevision": 1}})
    marker_catalog.refresh_catalog(fake_db, force=True)
    with patch("flymanager.app.routes.markers.db", fake_db):
        response = _client(app, fake_db).get("/markers")
    assert b"Key is required" in response.data
```

- [ ] **Step 5: Write the templates**

Create `flymanager/app/templates/markers/list.html` and `detail.html` extending `base.html`. **Read `flymanager/app/templates/settings/admin.html` first** and match its card/table/form structure, CSS class names and CSRF token handling rather than inventing new ones — the skeleton below fixes the data and control flow, not the markup vocabulary.

```jinja
{% extends "base.html" %}
{% block content %}
{% if invalid_definitions %}
  <div class="alert alert-warning">
    <strong>{{ invalid_definitions|length }} definition(s) could not be loaded.</strong>
    <ul>
      {% for entry in invalid_definitions %}
        <li>{{ entry.Key or "(no key)" }}: {{ entry.errors|join("; ") }}</li>
      {% endfor %}
    </ul>
  </div>
{% endif %}

<form method="get" action="{{ url_for('markers.marker_catalog') }}">
  <input type="search" name="q" value="{{ search_query }}" placeholder="Search markers">
  <select name="kind">
    <option value="">All kinds</option>
    {% for kind in kinds %}
      <option value="{{ kind }}" {% if kind == selected_kind %}selected{% endif %}>{{ kind }}</option>
    {% endfor %}
  </select>
  <select name="origin">
    <option value="">All origins</option>
    {% for origin in ("shipped", "user", "curated") %}
      <option value="{{ origin }}" {% if origin == selected_origin %}selected{% endif %}>{{ origin }}</option>
    {% endfor %}
  </select>
  <button type="submit">Filter</button>
</form>

<table>
  <thead>
    <tr><th>Key</th><th>Kind</th><th>Label</th><th>Body part</th>
        <th>Chromosome</th><th>Origin</th><th>Defined by</th><th></th></tr>
  </thead>
  <tbody>
    {% for definition in definitions %}
      <tr>
        <td><a href="{{ url_for('markers.marker_detail', key=definition.Key) }}">{{ definition.Key }}</a></td>
        <td>{{ definition.kind }}</td>
        <td>{{ definition.payload.get("display_label", "") }}</td>
        <td>{{ definition.payload.get("body_part", "") }}</td>
        <td>{{ definition.payload.get("chromosome", "") }}</td>
        <td><span class="badge badge-{{ definition.origin }}">{{ definition.origin }}</span></td>
        <td>{{ definition.CreatedBy or "FlyManager" }}</td>
        <td>
          {% if definition.editable_by_user %}
            <a href="{{ url_for('markers.marker_detail', key=definition.Key) }}">Edit</a>
          {% elif definition.origin == "shipped" %}
            <a href="{{ url_for('markers.marker_detail', key=definition.Key) }}">Override</a>
          {% endif %}
        </td>
      </tr>
    {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`CreatedBy` is not decoration: with global-immediate visibility, attribution is the safety mechanism, so every row shows who defined it. Shipped rows fall back to "FlyManager".

`detail.html` must render: `Key`, `kind`, display label, body part, chromosome, `origin` (shipped / user / curated, visually distinguished), and `CreatedBy` — attribution is the safety mechanism for global-immediate visibility, so it is not optional. Include the kind/origin/search filter form bound to the `kind`, `origin` and `q` query parameters. Shipped rows render read-only with an **Override** action linking to the detail page; rows with `editable_by_user` get **Edit**.

`detail.html` must render every envelope section (`match`, `payload`, `sorting`, `audit`, `imaging`, `provenance`), the audit trail (`CreatedBy`/`CreatedAt`/`UpdatedBy`/`UpdatedAt`/`CuratedBy`/`CuratedAt`), a **Promote** button when `can_promote`, a **Delete** button when `can_edit`, and an edit form when `can_edit`. When `is_shipped`, show an **Override** button that posts to `POST /markers` prefilled with the shipped values. Leave `imaging.images` and `expression` rendered but empty with a note that they are filled by later slices — do not remove them.

- [ ] **Step 6: Add the nav link**

In `flymanager/app/templates/base.html`, add a "Markers" entry next to the existing reviewer link, pointing at `url_for('markers.marker_catalog')`. The standardization reviewer page (`main.standardization_reviewer`) is where "this token is a real marker" lands, so also link to the catalog from there.

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_marker_routes.py tests/test_security.py tests/test_route.py -q -p no:cacheprovider`
Expected: the new file PASSES (11 tests); the others at baseline.

- [ ] **Step 8: Verify the pages actually render**

Use the `run-flymanager` skill to start the app, log in, and load `/markers` and `/markers/CyO`. A template that renders a `dict` as `{'a': 1}` blobs passes the tests above but is not a usable page.

- [ ] **Step 9: Commit**

```bash
git add flymanager/app/routes/markers.py flymanager/app/templates/markers/ \
        flymanager/app/__init__.py flymanager/app/templates/base.html \
        tests/test_marker_routes.py
git commit -m "feat: add the marker catalog UI and write routes"
```

---

### Task 16: Fold balancer ingestion into the catalog

**Files:**
- Modify: `flymanager/utils/phenotypes/data/balancer_ingest.py:393-405`
- Modify: `flymanager/utils/constraints/balancer_selection.py:20-48`
- Test: `tests/test_balancer_ingest_catalog.py`

**The trap the spec did not spell out:** `_candidate_documents` merges the `balancer_definitions` collection in, and `breakpoint_regions` / `breakpoint_text` come **only** from those documents — `_breakpoint_assessment` (`balancer_selection.py:71-72`) reads them, and they are surfaced in the result at `:184-185`. Deleting the staging read without carrying those fields into the catalog row silently degrades breakpoint-aware balancer scoring to the "no gene cytology" fallback. So the catalog `balancer` payload gains `breakpoint_regions` and `breakpoint_text`, and only then does the staging read go.

**Interfaces:**
- Consumes: `marker_catalog`, `bump_marker_catalog_revision`.
- Produces: `ingest_balancer_definitions(db, report, collection_name="balancer_definitions")` keeps its signature and its staging write, and additionally upserts each balancer into `marker_definitions` as a `balancer`-kind row with `origin: "curated"` and `CuratedBy: "bdsc_ingest"`.

**Why:** `balancer_definitions` is the existing half-overlay the spec calls out (`balancer_selection.py`). Task 5 stopped `balancer_selection` from reading `BALANCER_METADATA` directly, but it still reads `balancer_definitions` as a second source. After this task, `balancer_definitions` is an ingestion staging collection only and is never consulted at resolution time.

- [ ] **Step 1: Write the failing test**

Create `tests/test_balancer_ingest_catalog.py`:

```python
import pytest

from flymanager.utils.constraints.balancer_selection import _candidate_documents
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.data.balancer_ingest import \
    ingest_balancer_definitions
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _report():
    return {
        "definitions": {
            "source_url": "https://bdsc.example/balancers",
            "balancers": [
                {"symbol": "ZZ7", "chromosome": 3, "family": "ZZ7",
                 "default_markers": ["Sb"], "notes": ["From BDSC."]},
            ],
        }
    }


def test_ingest_still_populates_the_staging_collection():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    summary = ingest_balancer_definitions(db, _report())
    assert summary["inserted"] == 1
    assert db["balancer_definitions"].count_documents({"symbol": "ZZ7"}) == 1


def test_ingest_writes_a_curated_catalog_row():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())

    row = db["marker_definitions"].find_one({"Key": "ZZ7"})
    assert row["kind"] == "balancer"
    assert row["origin"] == "curated"
    assert row["payload"]["default_markers"] == ["Sb"]
    assert row["payload"]["chromosome"] == 3
    assert marker_catalog.read_catalog_revision(db) == 1


def test_the_ingested_balancer_resolves_through_the_catalog():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())
    marker_catalog.refresh_catalog(db, force=True)

    assert "ZZ7" in marker_catalog.get_catalog()["known_balancer_symbols"]
    candidates = _candidate_documents(3, db)
    assert "ZZ7" in {candidate.get("symbol") for candidate in candidates}


def test_re_ingesting_does_not_duplicate_catalog_rows():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())
    ingest_balancer_definitions(db, _report())
    assert db["marker_definitions"].count_documents({"Key": "ZZ7"}) == 1


def test_a_user_override_of_an_ingested_balancer_is_not_clobbered():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    ingest_balancer_definitions(db, _report())
    db["marker_definitions"].update_one(
        {"Key": "ZZ7"},
        {"$set": {"origin": "user", "CreatedBy": "alice",
                  "payload": {"family": "ZZ7", "chromosome": 3,
                              "default_markers": ["Cy"], "notes": []}}})

    ingest_balancer_definitions(db, _report())

    row = db["marker_definitions"].find_one({"Key": "ZZ7"})
    assert row["origin"] == "user"
    assert row["payload"]["default_markers"] == ["Cy"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_balancer_ingest_catalog.py -q -p no:cacheprovider`
Expected: `test_ingest_writes_a_curated_catalog_row` FAILS — `marker_definitions` is empty.

- [ ] **Step 3: Extend the ingestion**

In `flymanager/utils/phenotypes/data/balancer_ingest.py`:

```python
def _catalog_row_from_balancer(document):
    symbol = str(document.get("symbol") or "").strip()
    payload = {
        "family": document.get("family") or symbol,
        "chromosome": document.get("chromosome"),
        "default_markers": list(document.get("default_markers") or []),
        "notes": list(document.get("notes") or []),
        # Carried so balancer_selection keeps breakpoint-aware scoring once it
        # stops reading the balancer_definitions staging collection.
        "breakpoint_regions": list(document.get("breakpoint_regions") or []),
        "breakpoint_text": document.get("breakpoint_text", ""),
    }
    return {
        "Key": symbol,
        "kind": "balancer",
        "match": {"symbol": symbol, "aliases": list(document.get("aliases") or [])},
        "payload": payload,
        "sorting": {}, "audit": {}, "imaging": {"aliases": [], "images": []},
        "expression": {},
        "provenance": {"source": "bdsc_ingest"},
        "origin": "curated",
        "CuratedBy": "bdsc_ingest",
    }


def _upsert_catalog_balancers(db, balancers):
    """Mirror ingested balancers into marker_definitions.

    A row a user has taken ownership of (origin "user") is left alone: the
    ingest is a reference-data refresh, not an authority to overwrite someone's
    deliberate override.
    """
    from flymanager.utils.mongo.marker_definitions import \
        bump_marker_catalog_revision

    collection = db["marker_definitions"]
    written = 0
    for document in balancers:
        row = _catalog_row_from_balancer(document)
        if not row["Key"]:
            continue
        existing = collection.find_one({"Key": row["Key"]})
        if existing is not None and existing.get("origin") == "user":
            continue
        collection.update_one({"Key": row["Key"]}, {"$set": row}, upsert=True)
        written += 1

    if written:
        bump_marker_catalog_revision(db)
    return written
```

and call it from `ingest_balancer_definitions` just before the return, adding `"catalog_rows": _upsert_catalog_balancers(db, balancers)` to the returned summary.

- [ ] **Step 4: Stop reading the staging collection at resolution time**

In `flymanager/utils/constraints/balancer_selection.py`, delete the whole `balancer_definitions` block from `_candidate_documents` (the `get_collection(db, "balancer_definitions")` loop and its `hydrated` merge), leaving only the catalog loop from Task 5:

```python
def _candidate_documents(chromosome, db):
    """Balancer candidates for a chromosome, from the marker catalog.

    The balancer_definitions collection is an ingestion staging area only; its
    contents reach here as catalog rows written by ingest_balancer_definitions,
    so it is no longer read at resolution time.
    """
    candidates = {}
    for symbol, metadata in get_balancer_metadata_map().items():
        if metadata.get("chromosome") != chromosome:
            continue
        candidates[symbol] = metadata
    return list(candidates.values())
```

`db` is now unused by this function; keep the parameter (its callers pass it positionally) and note that in the docstring, or thread the removal through the call sites — do not change the signature halfway.

Add the test that this actually kept working:

```python
def test_breakpoint_data_survives_the_move_to_the_catalog():
    db = FakeDatabase({"balancer_definitions": [], "marker_definitions": [],
                       "settings": [{}]})
    report = _report()
    report["definitions"]["balancers"][0]["breakpoint_regions"] = ["61A", "89E"]
    report["definitions"]["balancers"][0]["breakpoint_text"] = "In(3LR)61A;89E"
    ingest_balancer_definitions(db, report)
    marker_catalog.refresh_catalog(db, force=True)

    candidate = next(c for c in _candidate_documents(3, db) if c["symbol"] == "ZZ7")
    assert candidate["breakpoint_regions"] == ["61A", "89E"]
    assert candidate["breakpoint_text"] == "In(3LR)61A;89E"


def test_the_staging_collection_is_no_longer_read():
    """A row present only in balancer_definitions must not reach scoring."""
    db = FakeDatabase({
        "balancer_definitions": [{"symbol": "STALE", "chromosome": 3}],
        "marker_definitions": [], "settings": [{}]})
    marker_catalog.refresh_catalog(db, force=True)
    assert "STALE" not in {c.get("symbol") for c in _candidate_documents(3, db)}
```

Without the second test, `test_the_ingested_balancer_resolves_through_the_catalog` passes vacuously — ZZ7 would arrive via the staging read even if the catalog path were broken.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_balancer_ingest_catalog.py tests/test_phase0_phase1_ingestion.py tests/test_constraints.py -q -p no:cacheprovider`
Expected: the new file PASSES (7 tests); the others at baseline.

- [ ] **Step 6: Run the full suite one final time**

Run the full command from Global Constraints.
Expected: baseline failure count. Investigate every new failure before committing.

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/phenotypes/data/balancer_ingest.py \
        flymanager/utils/constraints/balancer_selection.py \
        tests/test_balancer_ingest_catalog.py
git commit -m "feat: mirror ingested balancer definitions into the marker catalog"
```

---

## Follow-ups this plan deliberately does not do

- **Amend the reviewer-apply spec.** `docs/superpowers/specs/2026-08-17-reviewer-apply-and-registry-design.md` defines a per-user `marker_registry` carrying `IsMarker` and `Marker{...}`, which would be a second private marker store beside this one. That spec must drop those two fields and link into this catalog instead. The amendment lands with whichever of the two slices is implemented second; if the reviewer work is already merged when this lands, do the amendment as its own change.
- **Slice B (marker images)** and **slice C (binary expression systems)** — `imaging.images[]` and `expression{}` are deliberately left empty. Do not remove them as dead code.
- **Epistasis as data.** The two rules in `epistasis.py` stay Python; they reference catalog keys (`w_loss`, `mini_white`) that this plan preserves exactly.
- **`DRIVER_KEYWORDS`/`REPORTER_KEYWORDS`** stay in `flybase_pipeline.py`; they belong with slice C's expression model.
