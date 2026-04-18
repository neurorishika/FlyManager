import json
import os
import re
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_IMAGE_LIBRARY_PATH = REPO_ROOT / "data" / "phenotype_images"
DEFAULT_MANIFEST_FILENAME = "manifest.json"

PHENOTYPE_IMAGE_ALIASES = {
    "B": ["bar", "b"],
    "Bar": ["bar", "b"],
    "Cy": ["cy", "cyo"],
    "D": ["d", "dichaete"],
    "Dr": ["dr"],
    "Dr_Mio": ["drmio", "mio"],
    "Hu": ["hu"],
    "Kr_If": ["if"],
    "L": ["l", "lobe"],
    "l2me_1": ["me"],
    "amos_Roi": ["roi", "roughoid"],
    "epistasis:w_mini_white_rescue": ["miniwhite", "mini-white", "wplus", "w+"],
    "PPO1_Bc": ["bc"],
    "Sb": ["sb", "sb1"],
    "Ser": ["ser", "ser1"],
    "Tb": ["tb"],
    "Ubx": ["ubx"],
    "wg_Gla": ["gla"],
    "wg_Sp": ["sp"],
    "wa": ["wa", "whiteapricot", "white-apricot"],
    "mini_white": ["w+", "wplus", "miniwhite", "mini-white"],
    "w_loss": ["w-", "wminus", "w", "white"],
    "y_plus": ["y+", "yplus", "y"],
    "v_plus": ["v+", "vplus", "v"],
}

BODY_PART_ALIASES = {
    "wing": {"wing", "wings"},
    "eye": {"eye", "eyes"},
    "bristle": {"bristle", "bristles"},
    "body": {"body"},
    "haltere": {"haltere", "halteres"},
    "thorax": {"thorax", "shoulder"},
    "head": {"head"},
    "antenna": {"antenna", "head"},
}


def resolve_phenotype_image_library_path(base_dir=None):
    if base_dir is not None:
        return Path(base_dir)
    configured = os.getenv("FLYMANAGER_PHENOTYPE_IMAGE_LIBRARY_PATH", "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_IMAGE_LIBRARY_PATH


def _normalize_key(value):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("+", "plus").replace("-", "minus")
    return re.sub(r"[^a-z0-9]+", "", text)


def _manifest_entries(base_path):
    manifest_path = base_path / DEFAULT_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = payload if isinstance(payload, list) else payload.get("images", [])
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        image_path = Path(str(entry.get("image_path") or "").strip())
        if not image_path:
            continue
        resolved = (base_path / image_path).resolve()
        try:
            relative_path = resolved.relative_to(base_path.resolve())
        except ValueError:
            continue
        if resolved.suffix.lower() not in IMAGE_SUFFIXES or not resolved.is_file():
            continue
        alias_values = set(entry.get("aliases", []))
        for key in ("phenotype_key", "gene_stem", "allele_token", "display_label"):
            value = entry.get(key)
            if value:
                alias_values.add(value)
        normalized_aliases = {_normalize_key(value) for value in alias_values if _normalize_key(value)}
        normalized.append(
            {
                "relative_path": relative_path.as_posix(),
                "stem": image_path.stem,
                "normalized_stem": _normalize_key(image_path.stem),
                "aliases": normalized_aliases,
                "body_part": str(entry.get("body_part") or "").strip().lower(),
                "source_collection": str(entry.get("source_collection") or "").strip(),
                "source_name": str(entry.get("source_name") or "").strip(),
                "provenance": str(entry.get("provenance") or "").strip(),
                "credit": str(entry.get("credit") or "").strip(),
                "source_url": str(entry.get("source_url") or "").strip(),
                "notes": str(entry.get("notes") or "").strip(),
                "priority": int(entry.get("priority", 0) or 0),
                "manifest_entry": True,
            }
        )
    return normalized


def _scanned_entries(base_path):
    entries = []
    for path in sorted(base_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        relative_path = path.relative_to(base_path).as_posix()
        parts = path.relative_to(base_path).parts
        source_collection = parts[0] if len(parts) > 1 else ""
        body_part = parts[-2].lower() if len(parts) > 1 else ""
        entries.append(
            {
                "relative_path": relative_path,
                "stem": path.stem,
                "normalized_stem": _normalize_key(path.stem),
                "aliases": set(),
                "body_part": body_part,
                "source_collection": source_collection,
                "source_name": source_collection.replace("_", " ").title() if source_collection else "",
                "provenance": "Local phenotype image library",
                "credit": "",
                "source_url": "",
                "notes": "",
                "priority": 0,
                "manifest_entry": False,
            }
        )
    return entries


def _image_entries(base_path):
    manifest_entries = _manifest_entries(base_path)
    manifest_paths = {entry["relative_path"] for entry in manifest_entries}
    scanned_entries = [
        entry
        for entry in _scanned_entries(base_path)
        if entry["relative_path"] not in manifest_paths
    ]
    return manifest_entries + scanned_entries


def _marker_aliases(marker):
    aliases = set()
    for field in (
        "phenotype_key",
        "display_label",
        "gene_stem",
        "allele_token",
        "balancer_symbol",
        "alias_token",
    ):
        normalized = _normalize_key(marker.get(field))
        if normalized:
            aliases.add(normalized)
    for lookup_key in (marker.get("phenotype_key"), marker.get("display_label")):
        for alias in PHENOTYPE_IMAGE_ALIASES.get(str(lookup_key), []):
            normalized = _normalize_key(alias)
            if normalized:
                aliases.add(normalized)
    return aliases


def _body_part_matches(marker, entry):
    marker_body_part = str(marker.get("body_part") or "").strip().lower()
    entry_body_part = str(entry.get("body_part") or "").strip().lower()
    if not marker_body_part or not entry_body_part:
        return False
    aliases = BODY_PART_ALIASES.get(marker_body_part)
    if aliases:
        return entry_body_part in aliases
    aliases = BODY_PART_ALIASES.get(entry_body_part)
    if aliases:
        return marker_body_part in aliases
    return marker_body_part == entry_body_part


def _score_entry(marker, aliases, entry):
    entry_stem = entry.get("normalized_stem", "")
    if not entry_stem:
        return 0
    marker_body_part = str(marker.get("body_part") or "").strip().lower()
    entry_body_part = str(entry.get("body_part") or "").strip().lower()
    if marker_body_part and entry_body_part and not _body_part_matches(marker, entry):
        return 0

    allow_stem_fallback = not (entry.get("manifest_entry") and entry.get("aliases"))
    score = 0
    for alias in aliases:
        if alias and alias in entry.get("aliases", set()):
            score = max(score, 98)
        elif allow_stem_fallback and entry_stem == alias:
            score = max(score, 100)
        elif allow_stem_fallback and alias and entry_stem.startswith(alias):
            score = max(score, 88)
        elif allow_stem_fallback and alias and alias in entry_stem:
            score = max(score, 72)
    if score <= 0:
        return 0
    if score and _body_part_matches(marker, entry):
        score += 8
    if entry.get("source_collection") == "learning_to_fly":
        score += 4
    if entry.get("manifest_entry"):
        score += 6
    score += int(entry.get("priority", 0) or 0)
    return score


def _labels_to_marker_stubs(labels):
    markers = []
    for label in labels or []:
        normalized = str(label or "").strip()
        if not normalized or normalized.lower() in {
            "none",
            "unavailable",
            "no marker phenotype predicted",
        }:
            continue
        markers.append(
            {
                "display_label": normalized,
                "phenotype_key": normalized,
            }
        )
    return markers


def _split_summary_labels(summary_text):
    text = str(summary_text or "").strip()
    if not text or text.lower() in {"unavailable", "no marker phenotype predicted"}:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _select_reference_markers_from_prediction(prediction):
    for key in ("shared_markers", "female_markers", "expressed_markers"):
        markers = prediction.get(key) or []
        if markers:
            return markers

    for key in ("shared_marker_labels", "female_marker_labels", "marker_labels"):
        labels = prediction.get(key) or []
        if labels:
            return _labels_to_marker_stubs(labels)

    for key in ("shared_summary", "female_summary", "summary", "best_guess_summary"):
        labels = _split_summary_labels(prediction.get(key))
        if labels:
            return _labels_to_marker_stubs(labels)

    return []


def select_prediction_reference_images(prediction, *, base_dir=None, limit=6):
    return select_phenotype_reference_images(
        _select_reference_markers_from_prediction(prediction or {}),
        base_dir=base_dir,
        limit=limit,
    )


def select_phenotype_reference_images(markers, *, base_dir=None, limit=6):
    base_path = resolve_phenotype_image_library_path(base_dir)
    if not base_path.exists() or not base_path.is_dir():
        return []

    entries = _image_entries(base_path)
    matches = []
    seen_paths = set()
    seen_markers = set()

    for marker in markers or []:
        marker_key = (
            marker.get("phenotype_key"),
            marker.get("display_label"),
            marker.get("allele_token"),
        )
        if marker_key in seen_markers:
            continue
        seen_markers.add(marker_key)

        aliases = _marker_aliases(marker)
        if not aliases:
            continue

        best_entry = None
        best_score = 0
        for entry in entries:
            score = _score_entry(marker, aliases, entry)
            if score > best_score:
                best_entry = entry
                best_score = score
        if best_entry is None or best_score <= 0:
            continue

        relative_path = best_entry["relative_path"]
        if relative_path in seen_paths:
            continue
        seen_paths.add(relative_path)
        matches.append(
            {
                "relative_path": relative_path,
                "display_label": marker.get("display_label", marker.get("gene_stem", "?")),
                "body_part": marker.get("body_part", ""),
                "effect": marker.get("effect", ""),
                "source_collection": best_entry.get("source_collection", ""),
                "source_name": best_entry.get("source_name", ""),
                "provenance": best_entry.get("provenance", ""),
                "credit": best_entry.get("credit", ""),
                "source_url": best_entry.get("source_url", ""),
                "notes": best_entry.get("notes", ""),
                "match_score": best_score,
            }
        )
        if len(matches) >= limit:
            break

    return matches