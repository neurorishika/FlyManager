import re

from flymanager.utils.phenotypes.image_catalog import get_image_catalog
from flymanager.utils.phenotypes.marker_catalog import get_catalog

EXACT_KEY_SCORE = 1000
EPISTASIS_IMAGE_ALIASES = {"epistasis:w_mini_white_rescue": ["miniwhite", "mini-white", "wplus", "w+"]}
BODY_PART_ALIASES = {
    "wing": {"wing", "wings"}, "eye": {"eye", "eyes"},
    "bristle": {"bristle", "bristles"}, "body": {"body"},
    "haltere": {"haltere", "halteres"}, "thorax": {"thorax", "shoulder"},
    "head": {"head"}, "antenna": {"antenna", "head"},
}


def _phenotype_image_aliases():
    aliases = dict(get_catalog()["image_aliases"])
    aliases.update(EPISTASIS_IMAGE_ALIASES)
    return aliases


def _normalize_key(value):
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "", text.replace("+", "plus").replace("-", "minus")) if text else ""


def _marker_aliases(marker):
    aliases = set()
    for field in ("phenotype_key", "display_label", "gene_stem", "allele_token", "balancer_symbol", "alias_token"):
        normalized = _normalize_key(marker.get(field))
        if normalized:
            aliases.add(normalized)
    table = _phenotype_image_aliases()
    for lookup_key in (marker.get("phenotype_key"), marker.get("display_label")):
        for alias in table.get(str(lookup_key), []):
            normalized = _normalize_key(alias)
            if normalized:
                aliases.add(normalized)
    return aliases


def _entry_field(entry, name, default=""):
    return (entry.get("match") or {}).get(name, default)


def _body_part_matches(marker, entry):
    marker_part = str(marker.get("body_part") or "").strip().lower()
    entry_part = str(_entry_field(entry, "bodyPart")).strip().lower()
    if not marker_part or not entry_part:
        return False
    aliases = BODY_PART_ALIASES.get(marker_part)
    if aliases:
        return entry_part in aliases
    aliases = BODY_PART_ALIASES.get(entry_part)
    return marker_part in aliases if aliases else marker_part == entry_part


def _marker_definition_keys(marker):
    """The marker_definitions Keys this resolved marker could be filed under.

    Resolved marker dicts carry no "key" field -- get_visual_marker returns
    the payload plus gene_stem/allele_token, and nothing downstream stamps
    the definition Key onto it. So the exact-key tier has to reconstruct it
    from the fields that ARE the Key for each kind: allele_token for
    allele_markers ("Bl[1]"), gene_stem for gene_markers ("Sb"),
    balancer_symbol for balancers, alias_token for aliases. Without this the
    tier never fires on the prediction path and uploads are invisible
    everywhere except the marker detail page.

    Deliberately reconstructed here rather than stamped onto the marker in
    visual_markers: those dicts are serialized into PhenotypeCache, and this
    slice must not change their shape.
    """
    keys = []
    for field in ("key", "allele_token", "gene_stem", "balancer_symbol",
                  "alias_token"):
        value = str(marker.get(field) or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def _score_entry(marker, aliases, entry):
    entry_keys = _entry_field(entry, "markerKeys", []) or []
    if entry_keys and any(key in entry_keys for key in _marker_definition_keys(marker)):
        return EXACT_KEY_SCORE
    stem = str(_entry_field(entry, "stem"))
    if not stem:
        return 0
    marker_part = str(marker.get("body_part") or "").strip().lower()
    entry_part = str(_entry_field(entry, "bodyPart")).strip().lower()
    if marker_part and entry_part and not _body_part_matches(marker, entry):
        return 0
    entry_aliases = set(_entry_field(entry, "aliases", []) or [])
    manifest = bool(_entry_field(entry, "manifestEntry", False))
    allow_stem = not (manifest and entry_aliases)
    score = 0
    for alias in aliases:
        if alias and alias in entry_aliases:
            score = max(score, 98)
        elif allow_stem and stem == alias:
            score = max(score, 100)
        elif allow_stem and alias and stem.startswith(alias):
            score = max(score, 88)
        elif allow_stem and alias and alias in stem:
            score = max(score, 72)
    if score <= 0:
        return 0
    if _body_part_matches(marker, entry):
        score += 8
    if _entry_field(entry, "sourceCollection") == "learning_to_fly":
        score += 4
    if manifest:
        score += 6
    return score + int((entry.get("display") or {}).get("priority", 0) or 0)


def _labels_to_marker_stubs(labels):
    markers = []
    for label in labels or []:
        normalized = str(label or "").strip()
        if normalized and normalized.lower() not in {"none", "unavailable", "no marker phenotype predicted"}:
            markers.append({"display_label": normalized, "phenotype_key": normalized})
    return markers


def _split_summary_labels(summary_text):
    text = str(summary_text or "").strip()
    if not text or text.lower() in {"unavailable", "no marker phenotype predicted"}:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _select_reference_markers_from_prediction(prediction):
    for key in ("shared_markers", "female_markers", "expressed_markers"):
        if prediction.get(key):
            return prediction[key]
    for key in ("shared_marker_labels", "female_marker_labels", "marker_labels"):
        if prediction.get(key):
            return _labels_to_marker_stubs(prediction[key])
    for key in ("shared_summary", "female_summary", "summary", "best_guess_summary"):
        labels = _split_summary_labels(prediction.get(key))
        if labels:
            return _labels_to_marker_stubs(labels)
    return []


def _definition_key_for(marker):
    """The catalog Key this marker is filed under, or None.

    Used to link an imageless marker straight to the page where its image
    can be uploaded. _marker_definition_keys returns candidates in priority
    order (allele_token before gene_stem, and so on); the first that names a
    real definition wins. A marker with no definition -- a bare label parsed
    out of a summary string, say -- yields None, and the UI must then send
    the user to the catalog rather than to a 404.
    """
    catalog = get_catalog()
    sections = ("definitions", "gene_markers", "allele_markers", "aliases",
                "balancers")
    for candidate in _marker_definition_keys(marker):
        for section in sections:
            if candidate in (catalog.get(section) or {}):
                return candidate
    return None


def select_phenotype_reference_images(markers, *, limit=None):
    """One row per marker, whether or not an image was found.

    Rows for unmatched markers carry has_image=False with image_id and
    image_url set to None, so a caller can render a placeholder instead of
    silently omitting the marker. `limit` defaults to no cap: capping this
    list drops whole markers from a phenotype view, which is what made
    predicted markers disappear without explanation.
    """
    entries = get_image_catalog()["entries"]
    rows, seen_markers = [], set()
    for marker in markers or []:
        identity = (marker.get("phenotype_key"), marker.get("display_label"), marker.get("allele_token"))
        if identity in seen_markers:
            continue
        seen_markers.add(identity)
        aliases = _marker_aliases(marker)
        if not aliases and not _marker_definition_keys(marker):
            continue

        best_entry, best_score = None, 0
        for entry in entries:
            score = _score_entry(marker, aliases, entry)
            if score > best_score:
                best_entry, best_score = entry, score

        # Deliberately no de-duplication across markers: when two markers
        # share a best image (both carried on TM6B, for instance), showing
        # it twice is honest, whereas dropping the second marker loses it
        # from the view entirely.
        display = (best_entry or {}).get("display") or {}
        image_id = best_entry["imageId"] if best_entry else None
        rows.append({
            "image_id": image_id,
            "image_url": f"/markers/images/{image_id}" if image_id else None,
            "has_image": best_entry is not None,
            "marker_key": _definition_key_for(marker),
            "display_label": marker.get("display_label", marker.get("gene_stem", "?")),
            "body_part": marker.get("body_part", ""), "effect": marker.get("effect", ""),
            "source_collection": _entry_field(best_entry, "sourceCollection") if best_entry else "",
            "source_name": display.get("sourceName", ""), "provenance": display.get("provenance", ""),
            "credit": display.get("credit", ""), "source_url": display.get("sourceUrl", ""),
            "notes": display.get("caption", ""), "match_score": best_score,
        })
        if limit is not None and len(rows) >= limit:
            break
    return rows


def select_prediction_reference_images(prediction, *, limit=None):
    return select_phenotype_reference_images(_select_reference_markers_from_prediction(prediction or {}), limit=limit)
