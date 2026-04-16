from flymanager.utils.phenotypes.construct_markers import \
    extract_construct_markers
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.visual_markers import (BALANCER_MARKERS,
                                                        get_visual_marker)


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


def resolve_package_markers(package_str):
    parsed = parse_gene_package(package_str)
    markers = []

    for balancer in parsed["balancers"]:
        for marker_symbol in BALANCER_MARKERS.get(balancer["symbol"], []):
            marker = get_visual_marker(marker_symbol)
            if marker is None:
                continue
            marker.update(
                {
                    "source": "balancer_marker",
                    "token": balancer["token"],
                    "balancer_symbol": balancer["symbol"],
                }
            )
            markers.append(marker)

    for allele in parsed["classical_alleles"]:
        marker = get_visual_marker(allele["gene_stem"])
        if marker is None:
            continue
        marker.update(
            {
                "allele_spec": allele["allele_spec"],
                "source": "manual_dictionary",
                "token": allele["token"],
            }
        )
        markers.append(marker)

    for construct in parsed["constructs"]:
        markers.extend(extract_construct_markers(construct["full"]))

    return {
        "parsed": parsed,
        "markers": _deduplicate_markers(markers),
        "unresolved_tokens": parsed["unresolved"],
    }
