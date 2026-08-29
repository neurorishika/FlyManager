from flymanager.utils.constraints._shared import (DEFAULT_BALANCER_PRIORITY,
                                                  deepcopy_documents,
                                                  extract_cytology_tokens,
                                                  get_collection,
                                                  is_distal_to_gene,
                                                  iter_collection_documents,
                                                  lookup_gene_map_record,
                                                  normalize_chromosome_label,
                                                  parse_cytology_point,
                                                  parse_cytology_position)
from flymanager.utils.constraints.interchromosomal import \
    evaluate_interchromosomal_risk
from flymanager.utils.constraints.marker_stability import score_sorting_markers
from flymanager.utils.phenotypes.visual_markers import (
    get_balancer_metadata, get_balancer_metadata_map)


def _candidate_documents(chromosome, db):
    candidates = {}

    for symbol, metadata in get_balancer_metadata_map().items():
        if metadata.get("chromosome") != chromosome:
            continue
        candidates[symbol] = metadata

    collection = get_collection(db, "balancer_definitions")
    for document in deepcopy_documents(iter_collection_documents(collection)):
        document_chromosome = normalize_chromosome_label(document.get("chromosome"))
        if document_chromosome != chromosome:
            continue

        symbol = str(document.get("symbol") or "").strip()
        if not symbol:
            continue

        hydrated = candidates.setdefault(symbol, {})
        hydrated.update(document)

        curated = get_balancer_metadata(symbol)
        if curated is not None:
            hydrated.setdefault("family", curated.get("family"))
            hydrated.setdefault("default_markers", curated.get("default_markers", []))
            hydrated.setdefault("notes", curated.get("notes", []))
        hydrated["chromosome"] = chromosome

    return list(candidates.values())


def _fallback_priority_score(symbol, chromosome):
    ordered = DEFAULT_BALANCER_PRIORITY.get(chromosome, [])
    if not ordered:
        return 0.0
    if symbol not in ordered:
        return 0.04
    index = ordered.index(symbol)
    return round(max(0.06, 0.24 - index * 0.04), 2)


def _marker_labels(candidate):
    labels = []
    for field_name in ("default_markers", "marker_tokens"):
        for token in candidate.get(field_name, []) or []:
            token_text = str(token or "").strip()
            if token_text and token_text not in labels:
                labels.append(token_text)
    return labels


def _breakpoint_assessment(candidate, gene_position):
    regions = candidate.get("breakpoint_regions") or extract_cytology_tokens(candidate.get("breakpoint_text"))
    points = []
    for region in regions:
        points.extend(
            point
            for point in (parse_cytology_point(token) for token in extract_cytology_tokens(region))
            if point is not None
        )

    if gene_position is None:
        return {
            "score": 0.0,
            "same_arm": False,
            "distal_breakpoint": False,
            "brackets_gene": False,
            "warnings": ["No gene cytology was available, so breakpoint-aware scoring fell back to default balancer priority."],
        }

    same_arm_points = [point for point in points if point["arm"] == gene_position["arm"]]
    if not same_arm_points:
        return {
            "score": 0.05 if points else 0.0,
            "same_arm": False,
            "distal_breakpoint": False,
            "brackets_gene": False,
            "warnings": ["Candidate breakpoints do not clearly land on the same cytological arm as the target gene."],
        }

    distal_points = [point for point in same_arm_points if is_distal_to_gene(gene_position, point)]
    bracketing_orders = sorted(point["order"] for point in same_arm_points)
    brackets_gene = bracketing_orders[0] <= gene_position["order"] <= bracketing_orders[-1]

    score = 0.18
    warnings = []
    if distal_points:
        nearest_distance = min(abs(point["order"] - gene_position["order"]) / 100.0 for point in distal_points)
        score += 0.3
        score += min(0.24, 0.24 / (1.0 + nearest_distance))
    else:
        warnings.append("No distal breakpoint was found on the same arm, so distal suppression coverage is likely weaker.")

    if brackets_gene:
        score += 0.12

    return {
        "score": round(score, 2),
        "same_arm": True,
        "distal_breakpoint": bool(distal_points),
        "brackets_gene": brackets_gene,
        "warnings": warnings,
    }


def _gene_position(gene_symbol, db):
    record = lookup_gene_map_record(gene_symbol, db)
    if record is None:
        return None
    return parse_cytology_position(record.get("cytogenetic_loc") or record.get("recombination_loc"))


def select_optimal_balancer(
    allele_gene_symbol,
    chromosome,
    db,
    existing_balancers=None,
    excluded_balancers=None,
):
    normalized_chromosome = normalize_chromosome_label(chromosome)
    if normalized_chromosome is None:
        return {
            "gene_symbol": allele_gene_symbol,
            "chromosome": chromosome,
            "selected_balancer": None,
            "candidates": [],
            "warnings": ["Unsupported chromosome label for balancer selection."],
            "used_fallback": False,
            "gene_position": None,
        }

    excluded = {str(symbol).strip() for symbol in excluded_balancers or [] if str(symbol).strip()}
    gene_position = _gene_position(allele_gene_symbol, db)
    candidate_rows = []
    warnings = []

    for candidate in _candidate_documents(normalized_chromosome, db):
        symbol = str(candidate.get("symbol") or "").strip()
        if not symbol or symbol in excluded:
            continue

        stability = score_sorting_markers(_marker_labels(candidate))
        breakpoint_assessment = _breakpoint_assessment(candidate, gene_position)
        fallback_score = _fallback_priority_score(symbol, normalized_chromosome)
        interchromosomal = evaluate_interchromosomal_risk(
            balancers=list(existing_balancers or []) + [symbol]
        )
        penalty = round(interchromosomal["risk_score"] * 0.18, 2)
        total_score = round(
            breakpoint_assessment["score"]
            + fallback_score
            + stability["average_score"] * 0.35
            - penalty,
            2,
        )
        candidate_warnings = list(breakpoint_assessment["warnings"])
        candidate_warnings.extend(stability["warnings"])

        candidate_rows.append(
            {
                "symbol": symbol,
                "family": candidate.get("family", symbol),
                "chromosome": normalized_chromosome,
                "default_markers": _marker_labels(candidate),
                "breakpoint_regions": list(candidate.get("breakpoint_regions") or []),
                "breakpoint_text": candidate.get("breakpoint_text", ""),
                "marker_stability_score": stability["average_score"],
                "breakpoint_score": breakpoint_assessment["score"],
                "fallback_score": fallback_score,
                "interchromosomal_penalty": penalty,
                "score": total_score,
                "warnings": candidate_warnings,
            }
        )

    candidate_rows.sort(key=lambda row: (-row["score"], row["symbol"]))
    selected_balancer = candidate_rows[0] if candidate_rows else None

    if gene_position is None:
        warnings.append(
            "No FlyBase cytological position was found for this gene, so balancer selection used conservative chromosome-level defaults."
        )
    if selected_balancer is None:
        warnings.append("No candidate balancers were available for this chromosome.")

    return {
        "gene_symbol": allele_gene_symbol,
        "chromosome": normalized_chromosome,
        "gene_position": gene_position,
        "selected_balancer": selected_balancer,
        "candidates": candidate_rows,
        "warnings": warnings,
        "used_fallback": gene_position is None,
    }