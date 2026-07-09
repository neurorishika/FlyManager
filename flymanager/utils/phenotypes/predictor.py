from collections import Counter
from datetime import datetime, timezone

from flymanager.utils.genetics import cross_genotypes, qc_genotype
from flymanager.utils.phenotypes.compute import compute_marker_phenotype
from flymanager.utils.phenotypes.flybase_pipeline import \
    compute_flybase_pipeline_signature
from flymanager.utils.phenotypes.identifiability import \
    annotate_progeny_identifiability

PHENOTYPE_CACHE_VERSION = 2


def _cache_timestamp():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _marker_identity(marker):
    return (
        marker.get("phenotype_key"),
        marker.get("display_label"),
        marker.get("chromosome_index"),
    )


def _marker_sort_key(marker):
    return (
        marker.get("chromosome_index", 99),
        marker.get("display_label", marker.get("gene_stem", "")),
    )


def _deduplicate_markers(markers):
    deduplicated = []
    seen = set()
    for marker in sorted(markers, key=_marker_sort_key):
        key = _marker_identity(marker)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(marker)
    return deduplicated


def _marker_labels(markers):
    return [marker.get("display_label", marker.get("gene_stem", "?")) for marker in markers]


def _marker_summary(markers):
    labels = _marker_labels(markers)
    return ", ".join(labels) if labels else "No marker phenotype predicted"


def _deduplicate_labels(labels):
    deduplicated = []
    seen = set()
    for label in labels:
        normalized_label = str(label or "").strip()
        if not normalized_label or normalized_label in seen:
            continue
        seen.add(normalized_label)
        deduplicated.append(normalized_label)
    return deduplicated


def _source_confidence(marker):
    source_defaults = {
        "manual_dictionary": 0.88,
        "balancer_marker": 0.9,
        "construct_marker": 0.76,
        "epistasis_rule": 0.9,
    }
    marker_score = marker.get("scoring_confidence")
    if isinstance(marker_score, (int, float)):
        return float(marker_score)
    return source_defaults.get(marker.get("source"), 0.62)


def _confidence_label(score):
    if score >= 0.82:
        return "high"
    if score >= 0.62:
        return "medium"
    return "low"


def _marker_provenance_class(marker):
    source = str(marker.get("source") or "").strip()
    if source in {"manual_dictionary", "balancer_marker", "construct_marker", "epistasis_rule"}:
        if marker.get("mini_white"):
            return "dosage_sensitive"
        return "microscope_marker"
    if source == "flybase_allele_evidence":
        return "inference_only"
    return "auxiliary"


def _build_provenance_summary(markers, consequence_annotations):
    counts = Counter(_marker_provenance_class(marker) for marker in markers)
    if counts.get("microscope_marker"):
        primary_basis = "microscope_marker"
    elif counts.get("dosage_sensitive"):
        primary_basis = "dosage_sensitive"
    elif counts.get("inference_only"):
        primary_basis = "inference_only"
    else:
        primary_basis = "auxiliary"

    return {
        "counts": dict(counts),
        "primary_basis": primary_basis,
        "has_inference_only_support": bool(counts.get("inference_only")),
        "has_dosage_sensitive_markers": bool(counts.get("dosage_sensitive")),
        "consequence_count": len(consequence_annotations or []),
    }


def _estimate_confidence(markers, unresolved_tokens, sex_dependent=False, projection_limited=False):
    if markers:
        base_score = sum(_source_confidence(marker) for marker in markers) / len(markers)
    else:
        base_score = 0.62

    provenance_counts = Counter(_marker_provenance_class(marker) for marker in markers)
    if markers and provenance_counts.get("microscope_marker", 0) == 0:
        base_score -= 0.14
    elif provenance_counts.get("inference_only", 0) > provenance_counts.get("microscope_marker", 0):
        base_score -= 0.07
    if provenance_counts.get("dosage_sensitive", 0) and provenance_counts.get("microscope_marker", 0) == 0:
        base_score -= 0.08

    base_score -= min(len(unresolved_tokens), 3) * 0.12
    if sex_dependent:
        base_score -= 0.08
    if projection_limited:
        base_score -= 0.05

    score = round(_clamp(base_score, 0.25, 0.98), 2)
    return score, _confidence_label(score)


def _combine_warnings(*warning_lists):
    combined = []
    seen = set()
    for warnings in warning_lists:
        for warning in warnings:
            if warning in seen:
                continue
            seen.add(warning)
            combined.append(warning)
    return combined


def predict_individual_phenotype(genotype, sex):
    try:
        summary = compute_marker_phenotype(genotype, sex)
    except ValueError as exc:
        return {
            "normalized_genotype": genotype,
            "sex": sex,
            "summary": "Phenotype unavailable",
            "expressed_markers": [],
            "marker_labels": [],
            "unresolved_tokens": [],
            "mini_white_copy_count": 0,
            "warnings": [str(exc)],
            "confidence_score": 0.25,
            "confidence_label": "low",
            "error": str(exc),
        }

    warnings = []
    if summary["unresolved_tokens"]:
        warnings.append(
            "Unresolved genotype tokens: " + ", ".join(summary["unresolved_tokens"])
        )
    mini_white_rescue_marker = next(
        (
            marker
            for marker in summary["expressed_markers"]
            if marker.get("epistasis_rule") == "w_mini_white_rescue"
        ),
        None,
    )
    if mini_white_rescue_marker is not None:
        warnings.append(
            "mini-white rescue eye color is estimated from copy count and can vary with insertion site and chromatin context."
        )
    if summary["mini_white_copy_count"] > 1:
        warnings.append(
            "Multiple mini-white copies may change eye pigment intensity."
        )
    if summary.get("epistasis_events"):
        warnings.extend(event.get("label") for event in summary["epistasis_events"] if event.get("label"))
    if summary.get("split_system_annotations"):
        warnings.append(
            "Known split-system combinations are present and may introduce conditional expression effects."
        )
    if summary.get("viability_status") == "likely_inviable":
        warnings.append(
            "Current phenotype model predicts an inviability risk for this genotype."
        )
    if summary.get("fertility_status") == "reduced_or_sterile":
        warnings.append(
            "Current phenotype model predicts reduced fertility or sterility risk for this genotype."
        )
    if summary.get("stage_specific_effects"):
        warnings.append(
            "Stage-specific FlyBase consequence evidence: "
            + ", ".join(summary["stage_specific_effects"])
        )

    confidence_score, confidence_label = _estimate_confidence(
        summary["expressed_markers"],
        summary["unresolved_tokens"],
    )
    provenance_summary = _build_provenance_summary(
        summary["expressed_markers"],
        summary.get("consequence_annotations", []),
    )
    if provenance_summary["primary_basis"] == "inference_only":
        warnings.append(
            "Visible phenotype confidence is driven mostly by inference-only FlyBase evidence rather than direct microscope marker rules."
        )
    elif provenance_summary["primary_basis"] == "dosage_sensitive":
        warnings.append(
            "Visible phenotype confidence depends mostly on dosage-sensitive mini-white eye pigmentation."
        )

    return {
        **summary,
        "marker_labels": _marker_labels(summary["expressed_markers"]),
        "construct_annotation_labels": [
            annotation.get("label", "construct")
            for annotation in summary.get("construct_annotations", [])
        ],
        "split_system_labels": [
            annotation.get("label", "split-system")
            for annotation in summary.get("split_system_annotations", [])
        ],
        "provenance_summary": provenance_summary,
        "warnings": warnings,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "error": None,
    }


def predict_stock_phenotype(genotype):
    qc_passed, normalized_or_error = qc_genotype(genotype)
    if not qc_passed:
        return {
            "normalized_genotype": genotype,
            "best_guess_summary": "Phenotype unavailable",
            "best_guess_basis": "invalid_genotype",
            "female_summary": None,
            "male_summary": None,
            "female_construct_annotation_labels": [],
            "male_construct_annotation_labels": [],
            "female_split_system_labels": [],
            "male_split_system_labels": [],
            "shared_summary": None,
            "shared_markers": [],
            "shared_marker_labels": [],
            "female_only_markers": [],
            "female_only_labels": [],
            "male_only_markers": [],
            "male_only_labels": [],
            "warnings": [normalized_or_error],
            "unresolved_tokens": [],
            "confidence_score": 0.25,
            "confidence_label": "low",
            "projection_limited": True,
            "sex_dependent": False,
        }

    normalized = normalized_or_error
    chromosome_fields = [field.strip() for field in normalized.split(";")]
    x_field = chromosome_fields[0] if chromosome_fields else ""

    female_prediction = predict_individual_phenotype(normalized, "female")
    female_construct_annotation_labels = _deduplicate_labels(
        female_prediction.get("construct_annotation_labels", [])
    )
    female_split_system_labels = _deduplicate_labels(
        female_prediction.get("split_system_labels", [])
    )
    male_prediction = None
    male_construct_annotation_labels = []
    male_split_system_labels = []
    projection_limited = "/" in x_field

    warnings = []
    if projection_limited:
        warnings.append(
            "Male phenotype cannot be projected uniquely from a stock genotype with two X haplotypes."
        )
    else:
        male_prediction = predict_individual_phenotype(normalized, "male")
        male_construct_annotation_labels = _deduplicate_labels(
            male_prediction.get("construct_annotation_labels", [])
        )
        male_split_system_labels = _deduplicate_labels(
            male_prediction.get("split_system_labels", [])
        )

    if male_prediction is None:
        shared_markers = _deduplicate_markers(female_prediction["expressed_markers"])
        female_only_markers = []
        male_only_markers = []
        best_guess_summary = female_prediction["summary"]
        best_guess_basis = "female_reference"
        sex_dependent = False
        unresolved_tokens = list(female_prediction["unresolved_tokens"])
    else:
        female_markers = {
            _marker_identity(marker): marker
            for marker in female_prediction["expressed_markers"]
        }
        male_markers = {
            _marker_identity(marker): marker
            for marker in male_prediction["expressed_markers"]
        }

        shared_keys = set(female_markers) & set(male_markers)
        female_only_keys = set(female_markers) - shared_keys
        male_only_keys = set(male_markers) - shared_keys

        shared_markers = _deduplicate_markers(
            [female_markers[key] for key in shared_keys]
        )
        female_only_markers = _deduplicate_markers(
            [female_markers[key] for key in female_only_keys]
        )
        male_only_markers = _deduplicate_markers(
            [male_markers[key] for key in male_only_keys]
        )

        sex_dependent = bool(female_only_markers or male_only_markers)
        unresolved_tokens = sorted(
            set(female_prediction["unresolved_tokens"] + male_prediction["unresolved_tokens"])
        )

        if not sex_dependent:
            best_guess_summary = female_prediction["summary"]
            best_guess_basis = "shared"
        elif shared_markers:
            best_guess_summary = _marker_summary(shared_markers)
            best_guess_basis = "shared_core"
        else:
            best_guess_summary = (
                "Sex-dependent: female "
                + female_prediction["summary"]
                + "; male "
                + male_prediction["summary"]
            )
            best_guess_basis = "sex_dependent"

    warnings = _combine_warnings(
        warnings,
        female_prediction["warnings"],
        male_prediction["warnings"] if male_prediction else [],
    )
    if best_guess_summary == "No marker phenotype predicted":
        warnings.append(
            "Current marker model did not find a confident visible sorting phenotype for this stock."
        )

    confidence_markers = shared_markers or female_prediction["expressed_markers"]
    confidence_score, confidence_label = _estimate_confidence(
        confidence_markers,
        unresolved_tokens,
        sex_dependent=bool(male_prediction and sex_dependent),
        projection_limited=projection_limited,
    )

    return {
        "normalized_genotype": normalized,
        "best_guess_summary": best_guess_summary,
        "best_guess_basis": best_guess_basis,
        "female_summary": female_prediction["summary"],
        "male_summary": male_prediction["summary"] if male_prediction else None,
        "female_markers": female_prediction["expressed_markers"],
        "female_marker_labels": female_prediction["marker_labels"],
        "male_markers": male_prediction["expressed_markers"] if male_prediction else [],
        "male_marker_labels": male_prediction["marker_labels"] if male_prediction else [],
        "female_construct_annotation_labels": female_construct_annotation_labels,
        "male_construct_annotation_labels": male_construct_annotation_labels,
        "female_split_system_labels": female_split_system_labels,
        "male_split_system_labels": male_split_system_labels,
        "shared_summary": _marker_summary(shared_markers),
        "shared_markers": shared_markers,
        "shared_marker_labels": _marker_labels(shared_markers),
        "female_only_markers": female_only_markers,
        "female_only_labels": _marker_labels(female_only_markers),
        "male_only_markers": male_only_markers,
        "male_only_labels": _marker_labels(male_only_markers),
        "warnings": warnings,
        "unresolved_tokens": unresolved_tokens,
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "projection_limited": projection_limited,
        "sex_dependent": bool(male_prediction and sex_dependent),
    }


def build_stock_phenotype_cache(genotype):
    return {
        "version": PHENOTYPE_CACHE_VERSION,
        "computedAt": _cache_timestamp(),
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "genotype": genotype,
        "prediction": predict_stock_phenotype(genotype),
    }


def get_cached_stock_phenotype(record, strict=True):
    """Return the stored phenotype cache for a stock, if present.

    strict=True (the default, used by the explicit backfill/refresh paths)
    also requires the cache version and FlyBase pipeline signature to match
    the current pipeline, forcing a recompute of anything out of date.

    strict=False (used by ordinary read paths, e.g. explorer/view pages)
    serves whatever prediction is stored regardless of version/signature
    drift, so viewing a record never triggers a live recompute. Only the
    genotype match is still checked, since that's a correctness guard
    (has this record actually changed genotype since it was cached), not a
    staleness policy.
    """
    cache = record.get("PhenotypeCache")
    genotype = str(record.get("Genotype", ""))

    if not isinstance(cache, dict):
        return None
    if strict:
        if cache.get("version") != PHENOTYPE_CACHE_VERSION:
            return None
        if cache.get("pipelineSignature") and str(cache.get("pipelineSignature", "")) != compute_flybase_pipeline_signature():
            return None
    if str(cache.get("genotype", "")) != genotype:
        return None
    if not isinstance(cache.get("prediction"), dict):
        return None

    return cache


def summarize_parent_phenotypes(male_genotype, female_genotype):
    male_prediction = predict_individual_phenotype(male_genotype, "male")
    female_prediction = predict_individual_phenotype(female_genotype, "female")

    if male_prediction["summary"] == female_prediction["summary"]:
        combined_summary = male_prediction["summary"]
    else:
        combined_summary = (
            "Male " + male_prediction["summary"] + " / Female " + female_prediction["summary"]
        )

    source_counts = Counter(
        marker.get("source", "unknown")
        for marker in male_prediction["expressed_markers"] + female_prediction["expressed_markers"]
    )

    return {
        "male": male_prediction,
        "female": female_prediction,
        "summary": combined_summary,
        "source_counts": dict(source_counts),
    }


def _annotate_identifiability(annotated):
    return annotate_progeny_identifiability(annotated)


def annotate_offspring_predictions(predicted_offspring):
    annotated = []
    for genotype, sex, probability in predicted_offspring:
        phenotype = predict_individual_phenotype(genotype, sex)
        annotated.append(
            {
                "genotype": genotype,
                "sex": sex,
                "probability": probability,
                "probability_percent": round(probability * 100, 2),
                "phenotype": phenotype,
                "phenotype_summary": phenotype["summary"],
                "phenotype_confidence": phenotype["confidence_label"],
            }
        )
    return _annotate_identifiability(annotated)


def build_cross_phenotype_cache(male_genotype, female_genotype):
    from flymanager.utils.crossing.simulator import (
        evaluate_cross_directions, simulate_cross,
        summarize_direction_evaluation)

    simulation = simulate_cross(male_genotype, female_genotype)
    direction_evaluation = summarize_direction_evaluation(
        evaluate_cross_directions(male_genotype, female_genotype)
    )

    return {
        "version": PHENOTYPE_CACHE_VERSION,
        "computedAt": _cache_timestamp(),
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "maleGenotype": male_genotype,
        "femaleGenotype": female_genotype,
        "parentPhenotypes": simulation["parent_phenotypes"],
        "predictedOffspring": simulation["offspring"],
        "simulationSummary": simulation["summary"],
        "directionEvaluation": direction_evaluation,
    }


def get_cached_cross_phenotype(record, strict=True):
    """Return the stored phenotype cache for a cross, if present.

    See get_cached_stock_phenotype for the meaning of strict.
    """
    cache = record.get("PhenotypeCache")
    male_genotype = str(record.get("MaleGenotype", ""))
    female_genotype = str(record.get("FemaleGenotype", ""))

    if not isinstance(cache, dict):
        return None
    if strict:
        if cache.get("version") != PHENOTYPE_CACHE_VERSION:
            return None
        if cache.get("pipelineSignature") and str(cache.get("pipelineSignature", "")) != compute_flybase_pipeline_signature():
            return None
    if str(cache.get("maleGenotype", "")) != male_genotype:
        return None
    if str(cache.get("femaleGenotype", "")) != female_genotype:
        return None
    if not isinstance(cache.get("parentPhenotypes"), dict):
        return None
    if not isinstance(cache.get("predictedOffspring"), list):
        return None

    return cache