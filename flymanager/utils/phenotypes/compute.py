from collections import defaultdict

from flymanager.utils.genetics import qc_genotype
from flymanager.utils.phenotypes.resolver import resolve_package_markers


def _chromosome_copies(chromosome_text, chromosome_index, sex):
    field = (chromosome_text or "").strip()
    if chromosome_index == 0:
        if sex == "male":
            return [field or "+"]
        if "/" in field:
            return [part.strip() or "+" for part in field.split("/")]
        return [field or "+", field or "+"]

    if "/" in field:
        return [part.strip() or "+" for part in field.split("/")]
    return [field or "+", field or "+"]


def compute_marker_phenotype(genotype, sex):
    if sex not in {"male", "female"}:
        raise ValueError("Sex must be either 'male' or 'female'")

    qc_passed, normalized = qc_genotype(genotype)
    if not qc_passed:
        raise ValueError(normalized)

    chromosome_fields = [field.strip() for field in normalized.split(";")]
    chromosome_copy_counts = {}
    marker_candidates = []
    unresolved_tokens = []
    mini_white_copy_count = 0

    for chromosome_index, chromosome_text in enumerate(chromosome_fields):
        copies = _chromosome_copies(chromosome_text, chromosome_index, sex)
        chromosome_copy_counts[chromosome_index] = len(copies)
        for copy_index, package in enumerate(copies):
            resolved = resolve_package_markers(package)
            unresolved_tokens.extend(resolved["unresolved_tokens"])
            for marker in resolved["markers"]:
                candidate = dict(marker)
                candidate["chromosome_index"] = chromosome_index
                candidate["copy_index"] = copy_index
                marker_candidates.append(candidate)
                if candidate.get("mini_white"):
                    mini_white_copy_count += 1

    grouped_markers = defaultdict(
        lambda: {
            "copies": set(),
            "marker": None,
        }
    )

    for candidate in marker_candidates:
        key = (
            candidate.get("phenotype_key"),
            candidate.get("chromosome_index"),
        )
        grouped_markers[key]["marker"] = candidate
        grouped_markers[key]["copies"].add(candidate["copy_index"])

    expressed_markers = []
    for grouped in grouped_markers.values():
        marker = dict(grouped["marker"])
        chromosome_index = marker["chromosome_index"]
        total_copies = chromosome_copy_counts[chromosome_index]
        mutated_copy_count = len(grouped["copies"])
        dominance = marker.get("dominance", "dominant")
        is_expressed = dominance != "recessive" or mutated_copy_count >= total_copies
        if is_expressed:
            marker["expressed_copy_count"] = mutated_copy_count
            marker["chromosome_copy_count"] = total_copies
            expressed_markers.append(marker)

    expressed_markers.sort(
        key=lambda marker: (
            marker.get("chromosome_index", 99),
            marker.get("display_label", marker.get("gene_stem", "")),
        )
    )
    marker_summary = ", ".join(
        marker.get("display_label", marker.get("gene_stem", "?"))
        for marker in expressed_markers
    )

    return {
        "normalized_genotype": normalized,
        "sex": sex,
        "expressed_markers": expressed_markers,
        "all_marker_candidates": marker_candidates,
        "unresolved_tokens": sorted(set(unresolved_tokens)),
        "mini_white_copy_count": mini_white_copy_count,
        "summary": marker_summary or "No marker phenotype predicted",
    }
