from math import ceil

from flymanager.utils.constraints.marker_stability import score_sorting_markers
from flymanager.utils.constraints.target_validation import validate_target
from flymanager.utils.phenotypes.compute import compute_marker_phenotype


def estimate_target_yield(
    target_genotype,
    target_sex,
    db=None,
    *,
    target_fraction=None,
    available_stock_genotypes=None,
):
    validation = validate_target(
        target_genotype,
        target_sex,
        db=db,
        available_stock_genotypes=available_stock_genotypes,
    )
    phenotype = compute_marker_phenotype(validation["normalized_genotype"], target_sex)
    marker_stability = score_sorting_markers(phenotype["expressed_markers"])

    if not validation["viable"]:
        expected_fraction = 0.0
    else:
        expected_fraction = float(target_fraction if target_fraction is not None else 0.25)
        expected_fraction *= marker_stability["average_score"]
        if validation["requires_recombination"]:
            expected_fraction *= 0.45
        if validation["interchromosomal_risk"]["balancer_count"] == 2:
            expected_fraction *= 0.75
        elif validation["interchromosomal_risk"]["balancer_count"] >= 3:
            expected_fraction *= 0.55
        if not validation["fertile"]:
            expected_fraction *= 0.65
        if target_sex in {"male", "female"}:
            expected_fraction *= 0.85
        if phenotype["mini_white_copy_count"] and marker_stability["weakest_marker"] is not None:
            if marker_stability["weakest_marker"]["marker"] == "mini-white":
                expected_fraction *= 0.8

    expected_fraction = max(0.0, min(expected_fraction, 1.0))
    expected_targets_per_100 = round(expected_fraction * 100.0, 1)
    expected_targets_per_vial = round(expected_fraction * 40.0, 1)
    recommended_parallel_vials = max(1, ceil(12.0 / max(expected_targets_per_vial, 1.0)))
    if validation["requires_recombination"] or validation["interchromosomal_risk"]["balancer_count"] >= 2:
        recommended_parallel_vials = max(recommended_parallel_vials, 2)

    if expected_targets_per_vial >= 8:
        yield_label = "high"
    elif expected_targets_per_vial >= 3:
        yield_label = "moderate"
    else:
        yield_label = "low"

    warnings = list(validation["unsupported_reasons"])
    warnings.extend(marker_stability["warnings"])
    warnings.append(
        "Yield outputs are operational heuristics based on conservative vial assumptions, not deterministic brood-size predictions."
    )

    return {
        "normalized_genotype": validation["normalized_genotype"],
        "expected_fraction": round(expected_fraction, 3),
        "expected_targets_per_100_progeny": expected_targets_per_100,
        "expected_targets_per_vial": expected_targets_per_vial,
        "recommended_parallel_vials": recommended_parallel_vials,
        "yield_label": yield_label,
        "marker_stability": marker_stability,
        "validation": validation,
        "warnings": warnings,
    }