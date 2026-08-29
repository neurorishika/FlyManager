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
