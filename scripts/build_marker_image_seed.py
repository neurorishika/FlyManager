"""Build the deterministic committed marker-image seed from the old library."""
import json
import re
import shutil
from pathlib import Path

from flymanager.utils.phenotypes.image_normalize import normalize_image

SOURCE_DIR = Path("data/phenotype_images")
OUTPUT_DIR = Path("data/markers/images")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _normalize_key(value):
    text = str(value or "").strip().lower().replace("+", "plus").replace("-", "minus")
    return re.sub(r"[^a-z0-9]+", "", text) if text else ""


def _manifest_entries(base_path):
    manifest_path = base_path / "manifest.json"
    if not manifest_path.is_file():
        return []
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = payload if isinstance(payload, list) else payload.get("images", [])
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        image_path = Path(str(entry.get("image_path") or "").strip())
        resolved = (base_path / image_path).resolve()
        try:
            relative_path = resolved.relative_to(base_path.resolve())
        except ValueError:
            continue
        if resolved.suffix.lower() not in IMAGE_SUFFIXES or not resolved.is_file():
            continue
        alias_values = set(entry.get("aliases", []))
        for key in ("phenotype_key", "gene_stem", "allele_token", "display_label"):
            if entry.get(key):
                alias_values.add(entry[key])
        normalized.append({
            "relative_path": relative_path.as_posix(), "stem": image_path.stem,
            "normalized_stem": _normalize_key(image_path.stem),
            "aliases": {_normalize_key(v) for v in alias_values if _normalize_key(v)},
            "body_part": str(entry.get("body_part") or "").strip().lower(),
            "source_collection": str(entry.get("source_collection") or "").strip(),
            "source_name": str(entry.get("source_name") or "").strip(),
            "provenance": str(entry.get("provenance") or "").strip(),
            "credit": str(entry.get("credit") or "").strip(),
            "source_url": str(entry.get("source_url") or "").strip(),
            "notes": str(entry.get("notes") or "").strip(),
            "priority": int(entry.get("priority", 0) or 0), "manifest_entry": True,
        })
    return normalized


def _scanned_entries(base_path):
    entries = []
    for path in sorted(base_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        relative_path = path.relative_to(base_path).as_posix()
        parts = path.relative_to(base_path).parts
        source_collection = parts[0] if len(parts) > 1 else ""
        entries.append({
            "relative_path": relative_path, "stem": path.stem,
            "normalized_stem": _normalize_key(path.stem), "aliases": set(),
            "body_part": parts[-2].lower() if len(parts) > 1 else "",
            "source_collection": source_collection,
            "source_name": source_collection.replace("_", " ").title() if source_collection else "",
            "provenance": "Local phenotype image library", "credit": "",
            "source_url": "", "notes": "", "priority": 0, "manifest_entry": False,
        })
    return entries


def _image_entries(base_path):
    manifest = _manifest_entries(base_path)
    paths = {entry["relative_path"] for entry in manifest}
    return manifest + [e for e in _scanned_entries(base_path) if e["relative_path"] not in paths]


def main():
    entries = _image_entries(SOURCE_DIR)
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    merged = {}
    conflicts = []
    for position, entry in enumerate(entries):
        normalized = normalize_image((SOURCE_DIR / entry["relative_path"]).read_bytes(), max_bytes=None)
        image_id = f"img_{normalized.sha256[:16]}"
        filename = f"{image_id}.webp"
        (OUTPUT_DIR / filename).write_bytes(normalized.data)
        record = {
            "imageId": image_id, "file": filename, "sha256": normalized.sha256,
            "contentType": normalized.content_type, "bytes": normalized.bytes,
            "width": normalized.width, "height": normalized.height,
            "match": {"markerKeys": [], "aliases": sorted(entry["aliases"]),
                      "stem": entry["normalized_stem"], "bodyPart": entry["body_part"],
                      "manifestEntry": bool(entry["manifest_entry"]),
                      "sourceCollection": entry["source_collection"]},
            "display": {"label": entry["stem"], "caption": entry["notes"],
                        "credit": entry["credit"], "provenance": entry["provenance"],
                        "sourceName": entry["source_name"], "sourceUrl": entry["source_url"],
                        "sourcePath": entry["relative_path"], "priority": entry["priority"],
                        "sortOrder": position},
            "origin": "shipped",
        }
        prior = merged.get(image_id)
        if prior is None:
            merged[image_id] = record
        else:
            for field in ("stem", "bodyPart"):
                if prior["match"][field] != record["match"][field]:
                    conflicts.append((image_id, field, prior["match"][field], record["match"][field]))
            prior["match"]["aliases"] = sorted(set(prior["match"]["aliases"]) | set(record["match"]["aliases"]))
    if conflicts:
        raise SystemExit(f"byte-identical image metadata conflicts: {conflicts}")
    output = sorted(merged.values(), key=lambda row: row["display"]["sortOrder"])
    (OUTPUT_DIR / "index.json").write_text(
        json.dumps(output, indent=1, sort_keys=True), encoding="utf-8")
    print(f"{len(output)} entries, {sum(r['bytes'] for r in output) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
