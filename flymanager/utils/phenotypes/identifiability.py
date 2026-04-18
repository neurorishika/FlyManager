def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def marker_provenance_class(marker):
    source = str(marker.get("source") or "").strip()
    if source in {"manual_dictionary", "balancer_marker", "construct_marker", "epistasis_rule"}:
        if marker.get("mini_white"):
            return "dosage_sensitive"
        return "microscope_marker"
    if source == "flybase_allele_evidence":
        return "inference_only"
    return "auxiliary"


def _feature_sortability_score(feature_label, sortability):
    if str(feature_label or "").startswith("mini-white"):
        return 0.44
    if feature_label == "Tb":
        return 0.38

    return {
        "microscope_marker": 0.92,
        "dosage_sensitive": 0.48,
        "inference_only": 0.36,
        "auxiliary": 0.58,
    }.get(sortability, 0.58)


def phenotype_feature_keys(phenotype, sex):
    feature_keys = {}
    has_explicit_mini_white_marker = False
    for marker in phenotype.get("expressed_markers", []):
        label = marker.get("display_label", marker.get("gene_stem", "?"))
        key = f"marker:{label}"
        feature_keys[key] = {
            "label": label,
            "sortability": marker_provenance_class(marker),
        }
        if str(label).startswith("mini-white"):
            has_explicit_mini_white_marker = True

    if phenotype.get("mini_white_copy_count") and not has_explicit_mini_white_marker:
        key = f"mini_white:{phenotype.get('mini_white_intensity', 'unknown')}"
        feature_keys[key] = {
            "label": f"mini-white {str(phenotype.get('mini_white_intensity', 'unknown')).replace('_', ' ')}",
            "sortability": "dosage_sensitive",
        }

    feature_keys[f"sex:{sex}"] = {
        "label": f"sex {sex}",
        "sortability": "microscope_marker",
    }
    return feature_keys


def _feature_labels(feature_map, keys):
    labels = []
    for key in sorted(keys):
        if key not in feature_map:
            continue
        labels.append(feature_map[key]["label"])
    return labels


def _build_selection_instructions(distinguishing_features, exclusion_features, confusable_rows):
    if confusable_rows:
        return "No unique visual sort is predicted for this class."

    instructions = []
    if distinguishing_features:
        instructions.append("Select flies with " + ", ".join(distinguishing_features))
    if exclusion_features:
        instructions.append("exclude siblings showing " + ", ".join(exclusion_features))

    if not instructions:
        return "No stable distinguishing markers were found."
    return "; ".join(instructions) + "."


def _score_identifiability(
    distinguishing_features,
    exclusion_features,
    low_confidence_features,
    confusable_rows,
):
    if confusable_rows:
        return 0.0

    sorting_features = distinguishing_features or exclusion_features
    if not sorting_features:
        return 0.28

    feature_scores = [
        _feature_sortability_score(feature["label"], feature["sortability"])
        for feature in sorting_features
    ]
    score = 0.35 + (sum(feature_scores) / len(feature_scores)) * 0.6
    if len(sorting_features) == 1:
        score -= 0.06
    if low_confidence_features and len(low_confidence_features) == len(sorting_features):
        score -= 0.16
    if not distinguishing_features and exclusion_features:
        score -= 0.08
    return round(_clamp(score, 0.0, 0.98), 2)


def _confidence_label(score):
    if score >= 0.78:
        return "high"
    if score >= 0.56:
        return "medium"
    return "low"


def _evaluate_single_identifiability(progeny_classes, target_index, comparison_indices):
    target = progeny_classes[target_index]
    if not target.get("is_viable", True):
        return {
            "identifiable": False,
            "label": "Not a viable class",
            "confidence_label": "low",
            "score": 0.0,
            "distinguishing_features": [],
            "exclusion_features": [],
            "low_confidence_features": [],
            "confusable_genotypes": [],
            "confusable_rows": [],
            "confusable_classes": [],
            "warnings": ["This class is pruned as inviable and is not a practical sorting target."],
            "selection_instructions": "No unique visual sort is predicted for this class.",
            "depends_on_low_confidence_sorting": False,
            "uses_exclusion_sorting": False,
            "comparison_count": max(0, len(comparison_indices) - 1),
        }

    phenotype = target.get("phenotype", {})
    current_features = phenotype_feature_keys(phenotype, target.get("sex"))
    current_feature_keys = set(current_features)
    confusable_genotypes = []
    confusable_rows = []
    distinguishing_feature_sets = []
    exclusion_feature_sets = []

    for other_index in comparison_indices:
        if target_index == other_index:
            continue

        other = progeny_classes[other_index]
        other_features = phenotype_feature_keys(other.get("phenotype", {}), other.get("sex"))
        other_feature_keys = set(other_features)
        if current_feature_keys == other_feature_keys:
            confusable_genotypes.append(other.get("genotype", ""))
            confusable_rows.append(
                {
                    "genotype": other.get("genotype", ""),
                    "sex": other.get("sex", ""),
                    "probability_percent": other.get("probability_percent"),
                }
            )
            continue

        distinguishing_feature_sets.append(current_feature_keys - other_feature_keys)
        exclusion_feature_sets.append(other_feature_keys - current_feature_keys)

    identifying_keys = (
        set.intersection(*distinguishing_feature_sets)
        if distinguishing_feature_sets and all(distinguishing_feature_sets)
        else set()
    )
    exclusion_keys = set()
    if exclusion_feature_sets and not identifying_keys:
        for feature_set in exclusion_feature_sets:
            exclusion_keys.update(feature_set)

    distinguishing_features = [
        {
            "label": current_features[key]["label"],
            "sortability": current_features[key]["sortability"],
        }
        for key in sorted(identifying_keys)
        if key in current_features
    ]
    exclusion_features = []
    if exclusion_keys:
        seen_labels = set()
        for key in sorted(exclusion_keys):
            for other_index in comparison_indices:
                if target_index == other_index:
                    continue
                other_features = phenotype_feature_keys(
                    progeny_classes[other_index].get("phenotype", {}),
                    progeny_classes[other_index].get("sex"),
                )
                if key not in other_features:
                    continue
                label = other_features[key]["label"]
                if label in seen_labels:
                    continue
                seen_labels.add(label)
                exclusion_features.append(
                    {
                        "label": label,
                        "sortability": other_features[key]["sortability"],
                    }
                )

    sorting_features = distinguishing_features or exclusion_features
    low_confidence_features = [
        feature["label"]
        for feature in sorting_features
        if feature["sortability"] in {"dosage_sensitive", "inference_only"}
    ]
    warnings = []
    if confusable_genotypes:
        warnings.append("Phenotype is confusable with at least one sibling class.")
    elif exclusion_features and not distinguishing_features:
        warnings.append(
            "Sorting requires selecting against sibling markers rather than selecting a unique positive marker."
        )
    if low_confidence_features and len(low_confidence_features) == len(sorting_features):
        warnings.append(
            "Sibling separation depends only on low-confidence or dosage-sensitive features."
        )
    if len(sorting_features) == 1:
        feature_label = sorting_features[0]["label"]
        if str(feature_label).startswith("mini-white"):
            warnings.append(
                "mini-white dosage is the sole predicted discriminator and should be treated as low confidence."
            )
        if feature_label == "Tb":
            warnings.append(
                "Tubby is the sole predicted discriminator and is treated cautiously because Tb can revert on TM6 balancers."
            )

    score = _score_identifiability(
        distinguishing_features,
        exclusion_features,
        low_confidence_features,
        confusable_rows,
    )
    identifiable = not confusable_rows and bool(sorting_features or len(comparison_indices) <= 1)
    if confusable_rows:
        label = f"Confusable with {len(confusable_rows)} sibling class"
        if len(confusable_rows) != 1:
            label += "es"
    elif exclusion_features and not distinguishing_features:
        label = "Distinct by exclusion"
    elif low_confidence_features:
        label = "Sortable with caution"
    else:
        label = "Distinct from sibling classes"

    return {
        "identifiable": identifiable,
        "label": label,
        "confidence_label": _confidence_label(score),
        "score": score,
        "distinguishing_features": [feature["label"] for feature in distinguishing_features],
        "exclusion_features": [feature["label"] for feature in exclusion_features],
        "low_confidence_features": low_confidence_features,
        "confusable_genotypes": confusable_genotypes,
        "confusable_rows": confusable_rows,
        "confusable_classes": confusable_rows,
        "warnings": warnings,
        "selection_instructions": _build_selection_instructions(
            [feature["label"] for feature in distinguishing_features],
            [feature["label"] for feature in exclusion_features],
            confusable_rows,
        ),
        "depends_on_low_confidence_sorting": bool(
            sorting_features and len(low_confidence_features) == len(sorting_features)
        ),
        "uses_exclusion_sorting": bool(exclusion_features and not distinguishing_features),
        "comparison_count": max(0, len(comparison_indices) - 1),
    }


def check_identifiability(progeny_classes, target_indices=None, viable_only=True):
    if target_indices is None:
        target_indices = list(range(len(progeny_classes)))
    else:
        target_indices = list(target_indices)

    if viable_only:
        comparison_indices = [
            index
            for index, row in enumerate(progeny_classes)
            if row.get("is_viable", True)
        ]
    else:
        comparison_indices = list(range(len(progeny_classes)))

    results = [
        _evaluate_single_identifiability(progeny_classes, index, comparison_indices)
        for index in target_indices
    ]
    return {
        "target_indices": target_indices,
        "comparison_indices": comparison_indices,
        "all_identifiable": all(result["identifiable"] for result in results),
        "results": results,
    }


def annotate_progeny_identifiability(progeny_classes, viable_only=False):
    report = check_identifiability(
        progeny_classes,
        target_indices=range(len(progeny_classes)),
        viable_only=viable_only,
    )
    for index, result in zip(report["target_indices"], report["results"]):
        progeny_classes[index]["identifiability"] = result
    return progeny_classes