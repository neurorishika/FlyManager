from flymanager.utils.constraints import estimate_target_yield, validate_target
from flymanager.utils.genetics import cross_genotypes
from flymanager.utils.phenotypes.identifiability import \
    annotate_progeny_identifiability
from flymanager.utils.phenotypes.predictor import (
    predict_individual_phenotype, summarize_parent_phenotypes)


def _prune_reasons(validation, phenotype):
    reasons = list(validation.get("impossible_reasons") or [])
    if phenotype.get("viability_status") != "likely_viable":
        message = "Phenotype projection predicts this class is not likely viable."
        if message not in reasons:
            reasons.append(message)
    return reasons


def _normalize_viable_probabilities(rows):
    viable_total = sum(row["probability"] for row in rows if row.get("is_viable"))
    for row in rows:
        if viable_total and row.get("is_viable"):
            row["viable_probability"] = round(row["probability"] / viable_total, 6)
            row["viable_probability_percent"] = round(row["viable_probability"] * 100, 2)
        else:
            row["viable_probability"] = 0.0
            row["viable_probability_percent"] = 0.0
    return viable_total


def _summarize_simulation(rows):
    viable_rows = [row for row in rows if row.get("is_viable")]
    pruned_rows = [row for row in rows if not row.get("is_viable")]
    viable_fraction = round(sum(row["probability"] for row in viable_rows), 6)
    identifiable_viable_fraction = round(
        sum(
            row["probability"]
            for row in viable_rows
            if row.get("identifiability", {}).get("identifiable")
        ),
        6,
    )
    confident_sortable_fraction = round(
        sum(
            row["probability"]
            for row in viable_rows
            if row.get("identifiability", {}).get("identifiable")
            and not row.get("identifiability", {}).get("depends_on_low_confidence_sorting")
        ),
        6,
    )
    low_confidence_sorting_fraction = round(
        sum(
            row["probability"]
            for row in viable_rows
            if row.get("identifiability", {}).get("depends_on_low_confidence_sorting")
        ),
        6,
    )

    if viable_rows:
        weighted_identifiability_score = round(
            sum(
                row["viable_probability"] * row.get("identifiability", {}).get("score", 0.0)
                for row in viable_rows
            ),
            4,
        )
    else:
        weighted_identifiability_score = 0.0

    estimated_sortable_targets_per_vial = round(
        sum(
            row.get("yield_estimate", {}).get("expected_targets_per_vial", 0.0)
            * row.get("identifiability", {}).get("score", 0.0)
            for row in viable_rows
            if row.get("identifiability", {}).get("identifiable")
        ),
        2,
    )
    visible_marker_class_count = len(
        {
            row["phenotype_summary"]
            for row in viable_rows
            if row["phenotype_summary"] != "No marker phenotype predicted"
        }
    )

    best_sortable_class = None
    if viable_rows:
        best_sortable_class = max(
            viable_rows,
            key=lambda row: (
                row.get("yield_estimate", {}).get("expected_targets_per_vial", 0.0)
                * row.get("identifiability", {}).get("score", 0.0),
                row.get("identifiability", {}).get("score", 0.0),
                row["probability"],
            ),
        )
        best_sortable_class = {
            "genotype": best_sortable_class["genotype"],
            "sex": best_sortable_class["sex"],
            "phenotype_summary": best_sortable_class["phenotype_summary"],
            "probability_percent": best_sortable_class["probability_percent"],
            "viable_probability_percent": best_sortable_class["viable_probability_percent"],
            "identifiability": best_sortable_class.get("identifiability"),
            "yield_estimate": best_sortable_class.get("yield_estimate"),
        }

    return {
        "raw_class_count": len(rows),
        "viable_class_count": len(viable_rows),
        "pruned_class_count": len(pruned_rows),
        "viable_fraction": viable_fraction,
        "identifiable_viable_fraction": identifiable_viable_fraction,
        "confident_sortable_fraction": confident_sortable_fraction,
        "low_confidence_sorting_fraction": low_confidence_sorting_fraction,
        "weighted_identifiability_score": weighted_identifiability_score,
        "estimated_sortable_targets_per_vial": estimated_sortable_targets_per_vial,
        "visible_marker_class_count": visible_marker_class_count,
        "best_sortable_class": best_sortable_class,
    }


def simulate_cross(male, female, db=None):
    raw_rows = cross_genotypes(male, female)
    available_stock_genotypes = [male, female]
    offspring = []

    for genotype, sex, probability in raw_rows:
        phenotype = predict_individual_phenotype(genotype, sex)
        validation = validate_target(
            genotype,
            sex,
            db=db,
            available_stock_genotypes=available_stock_genotypes,
        )
        is_viable = bool(validation.get("viable")) and phenotype.get("viability_status") == "likely_viable"
        yield_estimate = estimate_target_yield(
            genotype,
            sex,
            db=db,
            target_fraction=probability,
            available_stock_genotypes=available_stock_genotypes,
        )
        offspring.append(
            {
                "genotype": genotype,
                "sex": sex,
                "probability": probability,
                "probability_percent": round(probability * 100, 2),
                "phenotype": phenotype,
                "phenotype_summary": phenotype["summary"],
                "phenotype_confidence": phenotype["confidence_label"],
                "validation": validation,
                "is_viable": is_viable,
                "pruned": not is_viable,
                "prune_reasons": _prune_reasons(validation, phenotype),
                "yield_estimate": yield_estimate,
            }
        )

    _normalize_viable_probabilities(offspring)
    annotate_progeny_identifiability(offspring, viable_only=True)

    viable_offspring = [row for row in offspring if row["is_viable"]]
    pruned_offspring = [row for row in offspring if not row["is_viable"]]
    return {
        "male_genotype": male,
        "female_genotype": female,
        "parent_phenotypes": summarize_parent_phenotypes(male, female),
        "offspring": offspring,
        "viable_offspring": viable_offspring,
        "pruned_offspring": pruned_offspring,
        "summary": _summarize_simulation(offspring),
    }


def cross_genotypes_with_phenotypes(male, female, db=None):
    return simulate_cross(male, female, db=db)["offspring"]


def _direction_signature(simulation):
    return [
        (
            row["genotype"],
            row["sex"],
            round(row["probability"], 6),
            row["phenotype_summary"],
            row["is_viable"],
        )
        for row in simulation["offspring"]
    ]


def _direction_score(summary):
    yield_component = min(1.0, summary["estimated_sortable_targets_per_vial"] / 8.0)
    visible_class_component = min(1.0, summary["visible_marker_class_count"] / 2.0)
    return round(
        yield_component * 0.38
        + summary["viable_fraction"] * 0.25
        + summary["confident_sortable_fraction"] * 0.2
        + summary["weighted_identifiability_score"] * 0.12
        + visible_class_component * 0.08
        - summary["low_confidence_sorting_fraction"] * 0.1,
        4,
    )


def _comparison_reasons(forward_summary, reverse_summary):
    reasons = []
    if abs(forward_summary["estimated_sortable_targets_per_vial"] - reverse_summary["estimated_sortable_targets_per_vial"]) >= 0.5:
        if reverse_summary["estimated_sortable_targets_per_vial"] > forward_summary["estimated_sortable_targets_per_vial"]:
            reasons.append("higher expected sortable yield per vial")
        else:
            reasons.append("lower expected sortable yield per vial")
    if abs(forward_summary["viable_fraction"] - reverse_summary["viable_fraction"]) >= 0.05:
        if reverse_summary["viable_fraction"] > forward_summary["viable_fraction"]:
            reasons.append("larger surviving progeny fraction")
        else:
            reasons.append("smaller surviving progeny fraction")
    if abs(forward_summary["confident_sortable_fraction"] - reverse_summary["confident_sortable_fraction"]) >= 0.05:
        if reverse_summary["confident_sortable_fraction"] > forward_summary["confident_sortable_fraction"]:
            reasons.append("more high-confidence sortable progeny")
        else:
            reasons.append("fewer high-confidence sortable progeny")
    if abs(forward_summary["weighted_identifiability_score"] - reverse_summary["weighted_identifiability_score"]) >= 0.05:
        if reverse_summary["weighted_identifiability_score"] > forward_summary["weighted_identifiability_score"]:
            reasons.append("stronger distinguishing marker profile")
        else:
            reasons.append("weaker distinguishing marker profile")
    if forward_summary["visible_marker_class_count"] != reverse_summary["visible_marker_class_count"]:
        if reverse_summary["visible_marker_class_count"] > forward_summary["visible_marker_class_count"]:
            reasons.append("more visible marker-defined progeny classes")
        else:
            reasons.append("fewer visible marker-defined progeny classes")
    return reasons


def evaluate_cross_directions(parent_a, parent_b, db=None):
    forward = simulate_cross(parent_a, parent_b, db=db)
    reverse = simulate_cross(parent_b, parent_a, db=db)
    forward_score = _direction_score(forward["summary"])
    reverse_score = _direction_score(reverse["summary"])
    same_outcome = _direction_signature(forward) == _direction_signature(reverse)

    if same_outcome or abs(forward_score - reverse_score) < 0.01:
        recommended_direction = "equivalent"
        recommended_cross = None
        rationale = ["Both parent orders produce materially equivalent viable and sortable progeny tables."]
    elif reverse_score > forward_score:
        recommended_direction = "reverse"
        recommended_cross = {"male": parent_b, "female": parent_a}
        rationale = [
            "Reverse order is recommended because it produces a better phenotype-sorting outcome.",
        ]
        rationale.extend(
            "Reverse order has " + reason + "."
            for reason in _comparison_reasons(forward["summary"], reverse["summary"])
        )
    else:
        recommended_direction = "forward"
        recommended_cross = {"male": parent_a, "female": parent_b}
        rationale = [
            "Forward order is recommended because it produces a better phenotype-sorting outcome.",
        ]
        rationale.extend(
            "Forward order has " + reason + "."
            for reason in _comparison_reasons(reverse["summary"], forward["summary"])
        )

    operational_notes = [
        "Virgin-collection burden is not yet modeled explicitly; this recommendation is based on viability, sortable yield, and phenotype identifiability.",
    ]

    return {
        "forward": {
            "male_genotype": parent_a,
            "female_genotype": parent_b,
            "score": forward_score,
            "summary": forward["summary"],
            "simulation": forward,
        },
        "reverse": {
            "male_genotype": parent_b,
            "female_genotype": parent_a,
            "score": reverse_score,
            "summary": reverse["summary"],
            "simulation": reverse,
        },
        "recommended_direction": recommended_direction,
        "recommended_cross": recommended_cross,
        "same_outcome": same_outcome,
        "rationale": rationale,
        "operational_notes": operational_notes,
    }


def summarize_direction_evaluation(direction_evaluation):
    def _summary(direction):
        payload = direction_evaluation[direction]
        return {
            "male_genotype": payload["male_genotype"],
            "female_genotype": payload["female_genotype"],
            "score": payload["score"],
            "summary": payload["summary"],
        }

    return {
        "forward": _summary("forward"),
        "reverse": _summary("reverse"),
        "recommended_direction": direction_evaluation["recommended_direction"],
        "recommended_cross": direction_evaluation["recommended_cross"],
        "same_outcome": direction_evaluation["same_outcome"],
        "rationale": list(direction_evaluation.get("rationale") or []),
        "operational_notes": list(direction_evaluation.get("operational_notes") or []),
    }