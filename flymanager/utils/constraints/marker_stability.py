from flymanager.utils.phenotypes.visual_markers import get_visual_marker

MARKER_STABILITY_SCORES = {
    "Cy": 0.95,
    "Sb": 0.8,
    "Ser": 0.78,
    "Hu": 0.72,
    "e": 0.7,
    "Ubx": 0.68,
    "mini-white": 0.58,
    "Tb": 0.5,
    "Bar": 0.45,
    "B": 0.4,
}

_MARKER_NOTES = {
    "Cy": ["Curly wings are treated as the most reliable dominant balancer marker."],
    "Sb": ["Stubble is a useful balancer marker but less stable than Curly in routine sorting."],
    "mini-white": ["mini-white eye pigmentation is dosage-sensitive and varies with insertion context."],
    "Tb": ["Tubby is down-weighted because TM6B/Tb reversion is a known high-frequency risk."],
    "B": ["Bar/B-style eye-shape calls are treated as weaker standalone sorting markers."],
}


def _marker_label(marker_or_label):
    if isinstance(marker_or_label, dict):
        if marker_or_label.get("mini_white"):
            return "mini-white"
        display_label = str(marker_or_label.get("display_label") or marker_or_label.get("gene_stem") or "").strip()
    else:
        display_label = str(marker_or_label or "").strip()

    if display_label.startswith("mini-white"):
        return "mini-white"
    return display_label


def assess_marker_stability(marker_or_label, *, balancer_symbol=None):
    label = _marker_label(marker_or_label)
    score = MARKER_STABILITY_SCORES.get(label)
    marker = marker_or_label if isinstance(marker_or_label, dict) else None

    if score is None and marker is not None:
        curated = get_visual_marker(marker.get("gene_stem"), allele_spec=marker.get("allele_spec"), token=marker.get("token"))
        if curated is not None:
            score = float(curated.get("scoring_confidence", 0.68))

    if score is None:
        score = 0.68

    resolved_balancer = str(balancer_symbol or (marker or {}).get("balancer_symbol") or "").strip()
    notes = list(_MARKER_NOTES.get(label, []))
    if label == "Tb" and resolved_balancer in {"TM6B", "TM6"}:
        score = min(score, 0.35)
        notes.append("TM6B explicitly keeps Tb because the marker can revert at high frequency.")

    if score >= 0.82:
        label_name = "stable"
    elif score >= 0.62:
        label_name = "moderate"
    else:
        label_name = "unstable"

    return {
        "marker": label or "unknown",
        "score": round(float(score), 2),
        "stability_label": label_name,
        "notes": notes,
    }


def score_sorting_markers(markers):
    assessments = [assess_marker_stability(marker) for marker in markers or []]
    if not assessments:
        return {
            "average_score": 0.68,
            "stability_label": "moderate",
            "weakest_marker": None,
            "markers": [],
            "warnings": [],
        }

    average_score = round(sum(item["score"] for item in assessments) / len(assessments), 2)
    weakest_marker = min(assessments, key=lambda item: item["score"])
    warnings = []
    if weakest_marker["score"] < 0.55:
        warnings.append(
            f"Sorting depends on {weakest_marker['marker']}, which is treated as an unstable marker."
        )
    if any(item["marker"] == "mini-white" for item in assessments):
        warnings.append(
            "mini-white should not be used as the only discriminator when a more stable visible marker is available."
        )

    if average_score >= 0.82:
        label_name = "stable"
    elif average_score >= 0.62:
        label_name = "moderate"
    else:
        label_name = "unstable"

    return {
        "average_score": average_score,
        "stability_label": label_name,
        "weakest_marker": weakest_marker,
        "markers": assessments,
        "warnings": warnings,
    }