from flymanager.utils.phenotypes.marker_catalog import get_catalog
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

DEFAULT_STABILITY_SCORE = 0.68


def _stability_entry(label):
    return get_catalog()["stability"].get(label) or {}


def _contextual_rules(label):
    """Caps that apply only when a particular balancer carries the marker.

    Read from their own snapshot index, not from the flat stability table:
    that table is only written for a definition that sets stabilityScore, so a
    definition with a cap and no base score would otherwise lose the cap.
    """
    return get_catalog()["contextual_stability"].get(label) or []


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
    entry = _stability_entry(label)
    score = entry.get("score")
    marker = marker_or_label if isinstance(marker_or_label, dict) else None

    if score is None and marker is not None:
        curated = get_visual_marker(marker.get("gene_stem"), allele_spec=marker.get("allele_spec"), token=marker.get("token"))
        if curated is not None:
            score = float(curated.get("scoring_confidence", DEFAULT_STABILITY_SCORE))

    if score is None:
        score = DEFAULT_STABILITY_SCORE

    resolved_balancer = str(balancer_symbol or (marker or {}).get("balancer_symbol") or "").strip()
    notes = list(entry.get("notes") or [])
    # Applied after the scoring_confidence fallback, in declaration order, so a
    # cap bounds whatever score the marker actually ended up with. whenBalancer
    # matches the RAW symbol rather than one canonicalized through
    # balancer_aliases: that is what the hardcoded Tb/TM6B rule did, and
    # routing it through the alias map would be a behaviour change smuggled in
    # under a refactor.
    for rule in _contextual_rules(label):
        if resolved_balancer and resolved_balancer in rule["when_balancer"]:
            score = min(score, rule["max_score"])
            if rule["note"]:
                notes.append(rule["note"])

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
            "average_score": DEFAULT_STABILITY_SCORE,
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