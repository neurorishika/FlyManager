import re

from flymanager.utils.phenotypes.construct_markers import \
    extract_construct_markers
from flymanager.utils.phenotypes.flybase_pipeline import (
    lookup_construct_annotations, lookup_flybase_allele_marker,
    lookup_flybase_marker_alias)
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.visual_markers import (
    BALANCER_MARKERS, get_balancer_metadata, get_reviewed_marker_alias,
    get_visual_marker)

ALLELE_TOKEN_RE = re.compile(r"^(?P<gene>[A-Za-z0-9.+*()_-]+)\[(?P<allele>[^\]]+)\]$")


def _is_construct_token(token):
    text = str(token or "").strip()
    return text.startswith(("P{", "PBac{", "Mi{", "TI{", "M{")) and "}" in text


def _deduplicate_markers(markers):
    deduplicated = []
    seen = set()
    for marker in markers:
        key = (
            marker.get("phenotype_key"),
            marker.get("display_label"),
            marker.get("source"),
            marker.get("construct_token"),
            marker.get("token"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(marker)
    return deduplicated


def _merge_marker_evidence(curated_marker, flybase_marker):
    if curated_marker is None:
        return flybase_marker
    if flybase_marker is None:
        return curated_marker

    merged = dict(curated_marker)
    merged.update(
        {
            "source": flybase_marker.get("source", curated_marker.get("source")),
            "flybase_evidence": True,
            "flybase_visible_rows": flybase_marker.get("flybase_visible_rows", 0),
            "flybase_reference_count": flybase_marker.get("flybase_reference_count", 0),
            "flybase_body_part_ids": flybase_marker.get("flybase_body_part_ids", []),
            "flybase_dominance_terms": flybase_marker.get("flybase_dominance_terms", []),
            "flybase_stock_occurrences": flybase_marker.get("flybase_stock_occurrences", 0),
            "scoring_confidence": max(
                float(curated_marker.get("scoring_confidence", 0.0)),
                float(flybase_marker.get("scoring_confidence", 0.0)),
            ),
        }
    )

    if flybase_marker.get("dominance") in {"dominant", "recessive"}:
        merged["dominance"] = flybase_marker["dominance"]

    return merged


def _resolve_alias_marker(alias_token, canonical_token, *, data_dir=None, cache_path=None, db=None):
    match = ALLELE_TOKEN_RE.match(str(canonical_token or "").strip())
    if match:
        gene_stem = match.group("gene")
        allele_spec = match.group("allele")
        marker = get_visual_marker(
            gene_stem,
            allele_spec=allele_spec,
            token=canonical_token,
        )
        marker = _merge_marker_evidence(
            marker,
            lookup_flybase_allele_marker(
                canonical_token,
                gene_stem=gene_stem,
                allele_spec=allele_spec,
                data_dir=data_dir,
                cache_path=cache_path,
                db=db,
            ),
        )
        if marker is None:
            return None
        marker.update(
            {
                "allele_spec": allele_spec,
                "token": canonical_token,
                "alias_token": alias_token,
            }
        )
        return marker

    marker = get_visual_marker(canonical_token, token=canonical_token)
    if marker is None:
        return None
    marker.update(
        {
            "token": canonical_token,
            "alias_token": alias_token,
        }
    )
    return marker


def _resolve_alias_construct(alias_token, canonical_token, *, data_dir=None, cache_path=None, db=None):
    markers = []
    for marker in extract_construct_markers(canonical_token):
        marker.update(
            {
                "alias_token": alias_token,
                "construct_token": canonical_token,
            }
        )
        markers.append(marker)

    construct_annotations = lookup_construct_annotations(
        canonical_token,
        data_dir=data_dir,
        cache_path=cache_path,
        db=db,
    )
    for annotation in construct_annotations:
        annotation.setdefault("alias_token", alias_token)

    return {
        "markers": markers,
        "construct_annotations": construct_annotations,
    }


def resolve_package_markers(package_str, data_dir=None, cache_path=None, db=None):
    parsed = parse_gene_package(package_str)
    markers = []
    construct_annotations = []
    unresolved_tokens = []

    for balancer in parsed["balancers"]:
        metadata = get_balancer_metadata(balancer["symbol"]) or {}
        for marker_symbol in metadata.get("default_markers", BALANCER_MARKERS.get(balancer["symbol"], [])):
            marker = get_visual_marker(marker_symbol)
            if marker is None:
                continue
            marker.update(
                {
                    "source": "balancer_marker",
                    "token": balancer["token"],
                    "balancer_symbol": balancer["symbol"],
                    "balancer_family": metadata.get("family"),
                    "balancer_chromosome": metadata.get("chromosome"),
                    "balancer_notes": list(metadata.get("notes", [])),
                }
            )
            markers.append(marker)

    for allele in parsed["classical_alleles"]:
        marker = get_visual_marker(
            allele["gene_stem"],
            allele_spec=allele["allele_spec"],
            token=allele["token"],
        )
        marker = _merge_marker_evidence(
            marker,
            lookup_flybase_allele_marker(
                allele["token"],
                gene_stem=allele["gene_stem"],
                allele_spec=allele["allele_spec"],
                data_dir=data_dir,
                cache_path=cache_path,
                db=db,
            ),
        )
        if marker is None:
            unresolved_tokens.append(allele["token"])
            continue
        marker.update(
            {
                "allele_spec": allele["allele_spec"],
                "source": marker.get("source", "manual_dictionary"),
                "token": allele["token"],
            }
        )
        markers.append(marker)

    for construct in parsed["constructs"]:
        markers.extend(extract_construct_markers(construct["full"]))
        construct_annotations.extend(
            lookup_construct_annotations(
                construct["full"],
                data_dir=data_dir,
                cache_path=cache_path,
                db=db,
            )
        )

    for token in parsed["unresolved"]:
        marker = get_visual_marker(token, token=token)
        if marker is not None:
            marker.update(
                {
                    "source": marker.get("source", "manual_dictionary"),
                    "token": token,
                }
            )
            markers.append(marker)
            continue

        reviewed_alias = get_reviewed_marker_alias(token)
        if reviewed_alias is not None:
            canonical_token = reviewed_alias.get("value")
            if _is_construct_token(canonical_token):
                alias_resolution = _resolve_alias_construct(
                    token,
                    canonical_token,
                    data_dir=data_dir,
                    cache_path=cache_path,
                    db=db,
                )
                markers.extend(alias_resolution["markers"])
                construct_annotations.extend(alias_resolution["construct_annotations"])
                continue

            marker = _resolve_alias_marker(
                token,
                canonical_token,
                data_dir=data_dir,
                cache_path=cache_path,
                db=db,
            )
            if marker is not None:
                marker["source"] = marker.get("source", "manual_dictionary")
                markers.append(marker)
                continue

        flybase_alias = lookup_flybase_marker_alias(
            token,
            data_dir=data_dir,
            cache_path=cache_path,
            db=db,
        )
        if flybase_alias is not None:
            canonical_token = flybase_alias.get("canonical_token")
            if _is_construct_token(canonical_token):
                alias_resolution = _resolve_alias_construct(
                    token,
                    canonical_token,
                    data_dir=data_dir,
                    cache_path=cache_path,
                    db=db,
                )
                markers.extend(alias_resolution["markers"])
                construct_annotations.extend(alias_resolution["construct_annotations"])
                continue

            marker = _resolve_alias_marker(
                token,
                canonical_token,
                data_dir=data_dir,
                cache_path=cache_path,
                db=db,
            )
            if marker is not None:
                marker["source"] = marker.get("source", "flybase_allele_evidence")
                if marker.get("display_label") == marker.get("gene_stem"):
                    marker["display_label"] = token
                markers.append(marker)
                continue

        unresolved_tokens.append(token)

    return {
        "parsed": parsed,
        "markers": _deduplicate_markers(markers),
        "construct_annotations": construct_annotations,
        "unresolved_tokens": unresolved_tokens,
    }
