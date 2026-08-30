"""Capture pre-migration image matching as the parity baseline.

This CANNOT be run against the current tree: data/phenotype_images and the
filesystem scorer are both gone. It is kept for reproducibility -- to
regenerate tests/fixtures/image_scoring_baseline.json, check out 697ab49
into a scratch worktree and run it there:

    git worktree add /tmp/fm-baseline 697ab49
    cd /tmp/fm-baseline && python scripts/capture_image_scoring_baseline.py

Stubs are taken from the resolved marker payloads in the catalog snapshot
rather than reconstructed from catalog.json, so they carry gene_stem and
allele_token exactly as the resolver emits them. An earlier version built
stubs by hand from camelCase keys that do not exist, which silently
produced near-empty matches and a parity guard that proved nothing.
"""
import json

from flymanager.utils.phenotypes.image_library import \
    select_phenotype_reference_images
from flymanager.utils.phenotypes.marker_catalog import get_catalog


def _stubs():
    catalog = get_catalog()
    stubs = []

    # gene_markers / allele_markers are keyed by the definition Key, and the
    # values are the resolved marker payloads -- the same dicts the resolver
    # hands the scorer. Use them rather than reconstructing from catalog.json.
    for key, marker in sorted(catalog["gene_markers"].items()):
        stub = dict(marker)
        stub["key"] = key
        stub.setdefault("gene_stem", key)
        stubs.append(stub)

    for key, marker in sorted(catalog["allele_markers"].items()):
        stub = dict(marker)
        stub["key"] = key
        stub["allele_token"] = key
        stub.setdefault("gene_stem", key.split("[")[0])
        stubs.append(stub)

    for key, marker in sorted(catalog.get("balancers", {}).items()):
        stub = dict(marker) if isinstance(marker, dict) else {}
        stub["key"] = key
        stub["balancer_symbol"] = key
        stubs.append(stub)

    for key, marker in sorted(catalog.get("aliases", {}).items()):
        stub = dict(marker) if isinstance(marker, dict) else {}
        stub["key"] = key
        stub["alias_token"] = key
        stubs.append(stub)

    return stubs


def main():
    records = []
    for stub in _stubs():
        matches = select_phenotype_reference_images([stub], limit=6)
        records.append({
            "marker": stub,
            "matches": [
                {"relative_path": m["relative_path"], "match_score": m["match_score"]}
                for m in matches
            ],
        })
    records.sort(key=lambda r: str(r["marker"]["key"]))
    with open("tests/fixtures/image_scoring_baseline.json", "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=1, sort_keys=True, default=str)
    print(len(records), "records,",
          sum(1 for r in records if r["matches"]), "with matches")


if __name__ == "__main__":
    main()
