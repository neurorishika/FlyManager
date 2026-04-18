from copy import deepcopy


def classify_mini_white_intensity(copy_count):
    count = int(copy_count or 0)
    if count <= 0:
        return "white"
    if count == 1:
        return "pale_orange"
    if count == 2:
        return "orange"
    if count == 3:
        return "dark_orange"
    return "red"


def format_mini_white_intensity(copy_count):
    return classify_mini_white_intensity(copy_count).replace("_", " ")


def _marker_identity(marker):
    return (
        marker.get("phenotype_key"),
        marker.get("display_label"),
        marker.get("chromosome_index"),
    )


def apply_epistasis_rules(markers, *, mini_white_copy_count=0):
    working_markers = [deepcopy(marker) for marker in markers]
    events = []
    suppressed_markers = []

    markers_by_stem = {}
    for marker in working_markers:
        gene_stem = str(marker.get("gene_stem") or "").strip()
        if gene_stem:
            markers_by_stem.setdefault(gene_stem, []).append(marker)

    white_markers = [
        marker for marker in working_markers if marker.get("phenotype_key") == "w_loss"
    ]
    mini_white_markers = [
        marker
        for marker in working_markers
        if marker.get("phenotype_key") == "mini_white" or marker.get("mini_white")
    ]

    if white_markers and mini_white_markers:
        suppressed_keys = {
            _marker_identity(marker) for marker in white_markers + mini_white_markers
        }
        rewritten_markers = []
        for marker in working_markers:
            marker_key = _marker_identity(marker)
            if marker_key in suppressed_keys:
                suppressed_markers.append(marker)
                continue
            rewritten_markers.append(marker)

        intensity_label = format_mini_white_intensity(mini_white_copy_count)
        rewritten_markers.append(
            {
                "gene_stem": "w",
                "body_part": "eye",
                "effect": f"{intensity_label} eye pigmentation from mini-white rescue in a white-eye background",
                "dominance": "dominant",
                "display_label": f"mini-white {intensity_label}",
                "phenotype_key": "epistasis:w_mini_white_rescue",
                "chromosome": None,
                "scoring_confidence": 0.86,
                "source": "epistasis_rule",
                "epistasis_rule": "w_mini_white_rescue",
                "sorting_role": "dosage_sensitive",
                "mini_white": True,
                "mini_white_intensity": classify_mini_white_intensity(mini_white_copy_count),
                "rescue_background": "w_loss",
            }
        )
        working_markers = rewritten_markers
        events.append(
            {
                "rule": "w_mini_white_rescue",
                "label": f"mini-white rescues white-eye loss with predicted {intensity_label} pigmentation",
                "suppressed_labels": [marker.get("display_label", "") for marker in white_markers + mini_white_markers],
                "introduced_label": f"mini-white {intensity_label}",
            }
        )

    if "cn" in markers_by_stem and "bw" in markers_by_stem and "w" not in markers_by_stem:
        suppressed_keys = {
            _marker_identity(marker)
            for marker in markers_by_stem["cn"] + markers_by_stem["bw"]
        }
        rewritten_markers = []
        for marker in working_markers:
            marker_key = _marker_identity(marker)
            if marker_key in suppressed_keys:
                suppressed_markers.append(marker)
                continue
            rewritten_markers.append(marker)

        rewritten_markers.append(
            {
                "gene_stem": "cn+bw",
                "body_part": "eye",
                "effect": "white eyes due to combined cinnabar and brown loss",
                "dominance": "recessive",
                "display_label": "cn+bw",
                "phenotype_key": "epistasis:cn_bw_white",
                "chromosome": None,
                "scoring_confidence": 0.9,
                "source": "epistasis_rule",
                "epistasis_rule": "cn_bw_white_eyes",
                "sorting_role": "microscope_marker",
                "mini_white_intensity": classify_mini_white_intensity(mini_white_copy_count),
            }
        )
        working_markers = rewritten_markers
        events.append(
            {
                "rule": "cn_bw_white_eyes",
                "label": "cn + bw masks intermediate eye colors to white",
                "suppressed_labels": [marker.get("display_label", "") for marker in suppressed_markers],
                "introduced_label": "cn+bw",
            }
        )

    return {
        "markers": working_markers,
        "events": events,
        "suppressed_markers": suppressed_markers,
    }