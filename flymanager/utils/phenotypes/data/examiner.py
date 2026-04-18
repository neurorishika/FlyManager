import csv
import gzip
import json
import re
from collections import Counter
from pathlib import Path

from flymanager.utils.phenotypes.compute import compute_marker_phenotype
from flymanager.utils.phenotypes.flybase_pipeline import \
    get_flybase_phenotype_cache
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.resolver import resolve_package_markers
from flymanager.utils.phenotypes.visual_markers import (
    ALLELE_VISUAL_MARKER_DICTIONARY, CRITICAL_MARKERS, REVIEWED_MARKER_ALIASES,
    VISUAL_MARKER_DICTIONARY)

EXPECTED_FLYBASE_FILES = {
    "genotype_phenotype_data": "genotype_phenotype_data",
    "fbal_to_fbgn": "fbal_to_fbgn",
    "allele_descriptions": "dmel_classical_and_insertion_allele_descriptions",
    "stocks": "stocks",
    "construct_descriptions": "transgenic_construct_descriptions",
    "split_system_combinations": "split_system_combinations",
    "gene_map_table": "gene_map_table",
}

ALLELE_TOKEN_RE = re.compile(r"(?P<gene>[A-Za-z0-9.+*_-]+)\[(?P<allele>[^\]]+)\]")
ANATOMY_TERM_RE = re.compile(r"FBbt:\d+", re.IGNORECASE)
DOMINANCE_TERM_RE = re.compile(
    r"\b(dominant|recessive|semi-dominant|semidominant|codominant|maternal effect)\b",
    re.IGNORECASE,
)


def _find_matching_file(data_dir, prefix):
    data_dir = Path(data_dir)
    matches = sorted(
        path
        for pattern in (f"{prefix}*.tsv", f"{prefix}*.tsv.gz")
        for path in data_dir.glob(pattern)
    )
    return matches[0] if matches else None


def _default_phenotype_cache_path():
    return Path(__file__).resolve().parents[4] / "data" / "flybase" / "PHENOTYPE_EVIDENCE_CACHE.json"


def _load_cached_alias_indexes(cache_path=None):
    resolved_cache_path = Path(cache_path) if cache_path else _default_phenotype_cache_path()
    if not resolved_cache_path.exists():
        return {}, {}

    payload = json.loads(resolved_cache_path.read_text(encoding="utf-8"))
    return payload.get("marker_alias_index", {}), payload.get("ambiguous_marker_aliases", {})


def _open_text_file(file_path):
    file_path = Path(file_path)
    if file_path.suffix == ".gz":
        return gzip.open(file_path, "rt", encoding="utf-8", newline="")
    return file_path.open("r", encoding="utf-8", newline="")


def _is_metadata_row(row):
    return bool(row) and len(row) == 1 and row[0].startswith("#")


def _extract_explicit_header(row):
    if not row or not row[0].startswith("#") or len(row) == 1:
        return None

    header = list(row)
    header[0] = header[0].lstrip("#")
    return header


def summarize_tsv_file(file_path, sample_size=5):
    file_path = Path(file_path)
    if not file_path.exists():
        return {"path": str(file_path), "exists": False}

    row_count = 0
    columns = []
    samples = []

    with _open_text_file(file_path) as handle:
        reader = csv.reader(handle, delimiter="\t")
        for row in reader:
            if not row:
                continue
            if _is_metadata_row(row):
                continue
            explicit_header = _extract_explicit_header(row)
            if explicit_header is not None:
                columns = explicit_header
                continue
            if not columns:
                columns = row
                continue

            row_count += 1
            if len(samples) < sample_size:
                samples.append(row)

    return {
        "path": str(file_path),
        "exists": True,
        "row_count": row_count,
        "column_count": len(columns),
        "columns": columns,
        "sample_rows": samples,
        "size_bytes": file_path.stat().st_size,
    }


def examine_flybase_directory(data_dir, markers=None):
    data_dir = Path(data_dir)
    markers = markers or CRITICAL_MARKERS
    summaries = {}
    located_files = {}

    for key, prefix in EXPECTED_FLYBASE_FILES.items():
        located_file = _find_matching_file(data_dir, prefix)
        located_files[key] = located_file
        summaries[key] = summarize_tsv_file(located_file) if located_file else {"exists": False}

    marker_coverage = {}
    qualifier_examples = []
    phenotype_path = located_files.get("genotype_phenotype_data")
    if phenotype_path is not None:
        marker_coverage, qualifier_examples = analyze_marker_coverage(phenotype_path, markers)

    return {
        "data_dir": str(data_dir),
        "files": summaries,
        "marker_coverage": marker_coverage,
        "qualifier_examples": qualifier_examples,
    }


def analyze_marker_coverage(file_path, markers):
    marker_patterns = {
        marker: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?:\[[^\]]+\])?", re.IGNORECASE)
        for marker in markers
    }
    coverage = {
        marker: {
            "total_rows": 0,
            "visible_rows": 0,
            "anatomy_rows": 0,
            "unconditional_rows": 0,
        }
        for marker in markers
    }
    qualifier_examples = []

    with _open_text_file(file_path) as handle:
        reader = csv.reader(handle, delimiter="\t")
        headers = None
        header_index = {}
        for row in reader:
            if not row:
                continue
            if _is_metadata_row(row):
                continue
            explicit_header = _extract_explicit_header(row)
            if explicit_header is not None:
                headers = explicit_header
                header_index = {name: index for index, name in enumerate(headers)}
                continue
            if headers is None:
                headers = row
                header_index = {name: index for index, name in enumerate(headers)}
                continue

            joined_text = " ".join(row)
            genotype_text = row[header_index["genotype_symbols"]] if "genotype_symbols" in header_index else row[0]
            phenotype_text = row[header_index["phenotype_name"]] if "phenotype_name" in header_index else joined_text
            qualifier_name_text = row[header_index["qualifier_names"]] if "qualifier_names" in header_index else joined_text
            qualifier_id_text = row[header_index["qualifier_ids"]] if "qualifier_ids" in header_index else joined_text
            analysis_text = " ".join([phenotype_text, qualifier_name_text, qualifier_id_text]).lower()

            if "visible" in analysis_text and len(qualifier_examples) < 10:
                qualifier_examples.append(row)

            for marker, pattern in marker_patterns.items():
                if not pattern.search(genotype_text):
                    continue
                coverage[marker]["total_rows"] += 1
                if "visible" in analysis_text:
                    coverage[marker]["visible_rows"] += 1
                if "fbbt:" in qualifier_id_text.lower():
                    coverage[marker]["anatomy_rows"] += 1
                if "with genotype" not in analysis_text and " with " not in analysis_text:
                    coverage[marker]["unconditional_rows"] += 1

    return coverage, qualifier_examples


def _extract_allele_tokens(genotype_text):
    tokens = []
    for match in ALLELE_TOKEN_RE.finditer(genotype_text or ""):
        tokens.append(
            {
                "token": match.group(0),
                "gene_stem": match.group("gene"),
                "allele_spec": match.group("allele"),
            }
        )
    return tokens


def _split_qualifier_field(value):
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"\s*[|;]\s*", text) if item.strip()]


def _extract_dominance_terms(*texts):
    terms = set()
    for text in texts:
        for match in DOMINANCE_TERM_RE.findall(str(text or "")):
            terms.add(match.lower())
    return terms


def _extract_body_part_ids(*texts):
    body_parts = set()
    for text in texts:
        body_parts.update(match.upper() for match in ANATOMY_TERM_RE.findall(str(text or "")))
    return body_parts


def _is_unconditional_visible_row(phenotype_name, qualifier_names, qualifier_ids):
    phenotype_text = str(phenotype_name or "").lower()
    qualifier_name_text = str(qualifier_names or "").lower()
    qualifier_id_text = str(qualifier_ids or "").lower()

    if "visible" not in phenotype_text:
        return False

    disqualifiers = [
        "with genotype",
        "with ",
        "somatic clone",
        "mosaic",
        "heat-sensitive",
        "cold-sensitive",
        "temperature-sensitive",
    ]
    analysis_text = " ".join([phenotype_text, qualifier_name_text, qualifier_id_text])
    return not any(token in analysis_text for token in disqualifiers)


def _collect_compatible_stock_usage(csv_path):
    allele_counter = Counter()
    gene_stem_counter = Counter()
    package_counter = Counter()

    for package in _iter_bloomington_packages(csv_path):
        package_counter[package] += 1
        parsed = parse_gene_package(package)
        for allele in parsed["classical_alleles"]:
            allele_counter[allele["token"]] += 1
            gene_stem_counter[allele["gene_stem"]] += 1

    return {
        "package_counter": package_counter,
        "allele_counter": allele_counter,
        "gene_stem_counter": gene_stem_counter,
    }


def generate_visible_marker_inventory(phenotype_file_path, bloomington_csv_path, high_priority_threshold=100):
    compatible_usage = _collect_compatible_stock_usage(bloomington_csv_path)
    allele_usage = compatible_usage["allele_counter"]
    gene_usage = compatible_usage["gene_stem_counter"]

    allele_inventory = {}
    gene_inventory = {}
    body_part_counter = Counter()
    dominance_counter = Counter()
    summary = Counter()

    with _open_text_file(phenotype_file_path) as handle:
        reader = csv.reader(handle, delimiter="\t")
        headers = None
        header_index = {}

        for row in reader:
            if not row:
                continue
            if _is_metadata_row(row):
                continue
            explicit_header = _extract_explicit_header(row)
            if explicit_header is not None:
                headers = explicit_header
                header_index = {name: index for index, name in enumerate(headers)}
                continue
            if headers is None:
                headers = row
                header_index = {name: index for index, name in enumerate(headers)}
                continue

            phenotype_name = row[header_index.get("phenotype_name", 2)]
            qualifier_names = row[header_index.get("qualifier_names", 4)] if len(row) > 4 else ""
            qualifier_ids = row[header_index.get("qualifier_ids", 5)] if len(row) > 5 else ""
            genotype_symbols = row[header_index.get("genotype_symbols", 0)]
            reference = row[header_index.get("reference", 6)] if len(row) > 6 else ""

            if "visible" not in str(phenotype_name or "").lower():
                continue

            summary["visible_rows"] += 1
            if not _is_unconditional_visible_row(phenotype_name, qualifier_names, qualifier_ids):
                continue

            summary["unconditional_visible_rows"] += 1

            allele_tokens = _extract_allele_tokens(genotype_symbols)
            if not allele_tokens:
                summary["rows_without_extractable_alleles"] += 1
                continue

            dominance_terms = _extract_dominance_terms(phenotype_name, qualifier_names, qualifier_ids)
            body_part_ids = _extract_body_part_ids(phenotype_name, qualifier_names, qualifier_ids)
            dominance_counter.update(dominance_terms)
            body_part_counter.update(body_part_ids)

            for allele in allele_tokens:
                token = allele["token"]
                gene_stem = allele["gene_stem"]

                allele_record = allele_inventory.setdefault(
                    token,
                    {
                        "token": token,
                        "gene_stem": gene_stem,
                        "visible_rows": 0,
                        "references": set(),
                        "dominance_terms": set(),
                        "body_part_ids": set(),
                        "compatible_allele_occurrences": allele_usage.get(token, 0),
                        "compatible_gene_occurrences": gene_usage.get(gene_stem, 0),
                    },
                )
                allele_record["visible_rows"] += 1
                allele_record["references"].add(reference)
                allele_record["dominance_terms"].update(dominance_terms)
                allele_record["body_part_ids"].update(body_part_ids)

                gene_record = gene_inventory.setdefault(
                    gene_stem,
                    {
                        "gene_stem": gene_stem,
                        "visible_rows": 0,
                        "alleles": set(),
                        "references": set(),
                        "dominance_terms": set(),
                        "body_part_ids": set(),
                        "compatible_gene_occurrences": gene_usage.get(gene_stem, 0),
                    },
                )
                gene_record["visible_rows"] += 1
                gene_record["alleles"].add(token)
                gene_record["references"].add(reference)
                gene_record["dominance_terms"].update(dominance_terms)
                gene_record["body_part_ids"].update(body_part_ids)

    allele_rows = []
    for record in allele_inventory.values():
        allele_rows.append(
            {
                "token": record["token"],
                "gene_stem": record["gene_stem"],
                "visible_rows": record["visible_rows"],
                "reference_count": len([ref for ref in record["references"] if ref]),
                "dominance_terms": sorted(record["dominance_terms"]),
                "body_part_ids": sorted(record["body_part_ids"]),
                "compatible_allele_occurrences": record["compatible_allele_occurrences"],
                "compatible_gene_occurrences": record["compatible_gene_occurrences"],
            }
        )

    gene_rows = []
    for record in gene_inventory.values():
        compatible_occurrences = record["compatible_gene_occurrences"]
        if compatible_occurrences >= high_priority_threshold:
            curation_bucket = "high"
        elif compatible_occurrences > 0:
            curation_bucket = "medium"
        else:
            curation_bucket = "low"

        gene_rows.append(
            {
                "gene_stem": record["gene_stem"],
                "visible_rows": record["visible_rows"],
                "allele_count": len(record["alleles"]),
                "alleles": sorted(record["alleles"]),
                "reference_count": len([ref for ref in record["references"] if ref]),
                "dominance_terms": sorted(record["dominance_terms"]),
                "body_part_ids": sorted(record["body_part_ids"]),
                "compatible_gene_occurrences": compatible_occurrences,
                "curation_bucket": curation_bucket,
            }
        )

    gene_rows.sort(
        key=lambda row: (
            -row["compatible_gene_occurrences"],
            -row["visible_rows"],
            row["gene_stem"],
        )
    )
    allele_rows.sort(
        key=lambda row: (
            -row["compatible_allele_occurrences"],
            -row["visible_rows"],
            row["token"],
        )
    )

    curation_queue = {
        bucket: [row for row in gene_rows if row["curation_bucket"] == bucket]
        for bucket in ("high", "medium", "low")
    }

    summary.update(
        {
            "visible_alleles": len(allele_rows),
            "visible_gene_stems": len(gene_rows),
            "compatible_visible_alleles": sum(
                1 for row in allele_rows if row["compatible_allele_occurrences"] > 0
            ),
            "compatible_visible_gene_stems": sum(
                1 for row in gene_rows if row["compatible_gene_occurrences"] > 0
            ),
            "high_priority_gene_stems": len(curation_queue["high"]),
            "medium_priority_gene_stems": len(curation_queue["medium"]),
            "low_priority_gene_stems": len(curation_queue["low"]),
            "compatible_stock_packages": sum(compatible_usage["package_counter"].values()),
        }
    )

    return {
        "phenotype_file_path": str(phenotype_file_path),
        "bloomington_csv_path": str(bloomington_csv_path),
        "summary": dict(summary),
        "top_gene_stems": gene_rows[:40],
        "top_alleles": allele_rows[:40],
        "body_part_ids": body_part_counter.most_common(25),
        "dominance_terms": dominance_counter.most_common(10),
        "curation_queue": curation_queue,
    }


def _iter_bloomington_packages(csv_path):
    with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ch_all = (row.get("Ch # all") or "").strip()
            genotype = (row.get("Genotype") or "").strip()
            if not ch_all or not genotype:
                continue
            if ch_all in {"Y", "mt", "U", "f"}:
                continue
            if any(flag in genotype for flag in ["Dp(", "Df(", "T(", "C(", "In(", "Tp(", "l("]):
                continue
            if ch_all == "wt":
                yield f"w[{genotype}]"
                continue

            genotype_components = [component.strip() for component in genotype.split(";")]
            for component in genotype_components:
                if not component:
                    continue
                for package in component.split("/"):
                    package = package.strip()
                    if package and package != "+":
                        yield package


def _normalize_unresolved_token(token):
    return str(token or "").strip().lower()


def _split_unresolved_token_counts(unresolved_token_counter, data_dir=None, cache_path=None):
    ambiguous_aliases = set()
    try:
        cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path)
        ambiguous_aliases = set((cache.get("ambiguous_marker_aliases") or {}).keys())
    except Exception:
        ambiguous_aliases = set()

    ambiguous_counter = Counter()
    unknown_counter = Counter()
    for token, count in unresolved_token_counter.items():
        normalized = _normalize_unresolved_token(token)
        if normalized and normalized in ambiguous_aliases:
            ambiguous_counter[token] = count
        else:
            unknown_counter[token] = count

    return ambiguous_counter, unknown_counter


def _record_identifier(row):
    for field in ("UniqueID", "Stk #", "StockID", "FBst", "Name", "AltReference"):
        value = str(row.get(field, "") or "").strip()
        if value:
            return value
    return "(unknown)"


def _iter_genotype_packages(genotype):
    genotype_text = str(genotype or "").strip()
    if not genotype_text:
        return

    group_pairs = {"{": "}", "[": "]", "(": ")"}

    def _split_top_level(text, separators):
        tokens = []
        current = []
        closing_stack = []

        for character in text:
            if character in group_pairs:
                closing_stack.append(group_pairs[character])
                current.append(character)
                continue

            if character in group_pairs.values():
                if closing_stack and character == closing_stack[-1]:
                    closing_stack.pop()
                current.append(character)
                continue

            if not closing_stack and character in separators:
                token = "".join(current).strip()
                if token:
                    tokens.append(token)
                current = []
                continue

            current.append(character)

        token = "".join(current).strip()
        if token:
            tokens.append(token)
        return tokens

    for component in _split_top_level(genotype_text, {";"}):
        component = component.strip()
        if not component:
            continue
        for package in _split_top_level(component, {"/"}):
            package = package.strip()
            if package and package != "+":
                yield package


def _append_token_sample(samples_by_token, token, row, genotype):
    token_samples = samples_by_token.setdefault(token, [])
    if len(token_samples) >= 3:
        return

    identifier = _record_identifier(row)
    summary = f"{identifier}: {str(genotype or '').strip()}"
    if summary not in token_samples:
        token_samples.append(summary)


def _render_standardization_entries(counter, *, samples_by_token, replacement_map=None, candidate_map=None):
    entries = []
    for token, count in counter.most_common():
        entry = {
            "token": token,
            "count": count,
            "samples": list(samples_by_token.get(token, [])),
        }
        if replacement_map is not None:
            entry["replacement"] = replacement_map.get(token)
        if candidate_map is not None:
            entry["candidates"] = list(candidate_map.get(token, []))
        entries.append(entry)
    return entries


def _audit_stock_standardization_rows(rows, *, genotype_field, cache_path=None):
    alias_index, ambiguous_aliases = _load_cached_alias_indexes(cache_path)
    known_gene_stems = set(VISUAL_MARKER_DICTIONARY)
    known_allele_tokens = set(ALLELE_VISUAL_MARKER_DICTIONARY)

    summary = Counter()
    safe_replacement_counter = Counter()
    ambiguous_shorthand_counter = Counter()
    unknown_token_counter = Counter()
    unmodeled_standard_allele_counter = Counter()

    safe_replacements = {}
    ambiguous_candidates = {}
    samples_by_token = {}

    for row in rows:
        summary["records_scanned"] += 1
        genotype = str(row.get(genotype_field, "") or "").strip()
        if not genotype:
            summary["missing_genotype"] += 1
            continue

        summary["records_with_genotype"] += 1
        record_flagged = False

        for package in _iter_genotype_packages(genotype):
            parsed = parse_gene_package(package)

            for allele in parsed["classical_alleles"]:
                token = allele["token"]
                if token in known_allele_tokens or allele["gene_stem"] in known_gene_stems:
                    continue

                unmodeled_standard_allele_counter[token] += 1
                _append_token_sample(samples_by_token, token, row, genotype)
                record_flagged = True

            for token in parsed["unresolved"]:
                if token in known_gene_stems:
                    continue

                normalized = _normalize_unresolved_token(token)
                replacement = None
                if token in REVIEWED_MARKER_ALIASES:
                    replacement = REVIEWED_MARKER_ALIASES[token].get("value")
                else:
                    replacement = (alias_index.get(normalized) or {}).get("canonical_token")

                if replacement:
                    safe_replacement_counter[token] += 1
                    safe_replacements[token] = replacement
                    _append_token_sample(samples_by_token, token, row, genotype)
                    record_flagged = True
                    continue

                if normalized in ambiguous_aliases:
                    ambiguous_shorthand_counter[token] += 1
                    ambiguous_candidates[token] = list(ambiguous_aliases.get(normalized, []))
                    _append_token_sample(samples_by_token, token, row, genotype)
                    record_flagged = True
                    continue

                unknown_token_counter[token] += 1
                _append_token_sample(samples_by_token, token, row, genotype)
                record_flagged = True

        if record_flagged:
            summary["records_with_standardization_candidates"] += 1

    return {
        "summary": dict(summary),
        "safe_replacement_total": sum(safe_replacement_counter.values()),
        "safe_replacement_unique": len(safe_replacement_counter),
        "ambiguous_shorthand_total": sum(ambiguous_shorthand_counter.values()),
        "ambiguous_shorthand_unique": len(ambiguous_shorthand_counter),
        "unknown_token_total": sum(unknown_token_counter.values()),
        "unknown_token_unique": len(unknown_token_counter),
        "unmodeled_standard_allele_total": sum(unmodeled_standard_allele_counter.values()),
        "unmodeled_standard_allele_unique": len(unmodeled_standard_allele_counter),
        "safe_replacements": _render_standardization_entries(
            safe_replacement_counter,
            samples_by_token=samples_by_token,
            replacement_map=safe_replacements,
        ),
        "ambiguous_shorthand": _render_standardization_entries(
            ambiguous_shorthand_counter,
            samples_by_token=samples_by_token,
            candidate_map=ambiguous_candidates,
        ),
        "unknown_tokens": _render_standardization_entries(
            unknown_token_counter,
            samples_by_token=samples_by_token,
        ),
        "unmodeled_standard_alleles": _render_standardization_entries(
            unmodeled_standard_allele_counter,
            samples_by_token=samples_by_token,
        ),
    }


def audit_stock_csv_standardization(csv_path, genotype_field="Genotype", cache_path=None):
    with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        report = _audit_stock_standardization_rows(
            reader,
            genotype_field=genotype_field,
            cache_path=cache_path,
        )

    report.update(
        {
            "csv_path": str(csv_path),
            "genotype_field": genotype_field,
            "source_kind": "csv",
        }
    )
    return report


def audit_live_stock_standardization(db, collection_name="stocks", genotype_field="Genotype", cache_path=None, query=None):
    collection = db[collection_name]
    rows = collection.find(query or {}, {"UniqueID": 1, "Name": 1, genotype_field: 1})
    report = _audit_stock_standardization_rows(
        rows,
        genotype_field=genotype_field,
        cache_path=cache_path,
    )
    report.update(
        {
            "collection": collection_name,
            "genotype_field": genotype_field,
            "source_kind": "mongo",
            "query": query or {},
        }
    )
    return report


def render_stock_standardization_markdown(report):
    summary = report["summary"]
    lines = [
        "# Stock Token Standardization Audit",
        "",
    ]

    if report.get("source_kind") == "mongo":
        lines.extend(
            [
                f"- Mongo collection: {report['collection']}",
                f"- Query: {report.get('query', {})}",
            ]
        )
    else:
        lines.append(f"- CSV path: {report['csv_path']}")

    lines.extend(
        [
        f"- Genotype field: {report['genotype_field']}",
        f"- Records scanned: {summary.get('records_scanned', 0)}",
        f"- Records with genotype: {summary.get('records_with_genotype', 0)}",
        f"- Records with standardization candidates: {summary.get('records_with_standardization_candidates', 0)}",
        f"- Safe shorthand replacements: {report['safe_replacement_total']} occurrences across {report['safe_replacement_unique']} unique tokens",
        f"- Ambiguous shorthand tokens: {report['ambiguous_shorthand_total']} occurrences across {report['ambiguous_shorthand_unique']} unique tokens",
        f"- Unknown bare tokens: {report['unknown_token_total']} occurrences across {report['unknown_token_unique']} unique tokens",
        f"- Standard-format but unmodeled alleles: {report['unmodeled_standard_allele_total']} occurrences across {report['unmodeled_standard_allele_unique']} unique tokens",
        "",
        "## Safe Replacements",
        "",
        ]
    )

    if not report["safe_replacements"]:
        lines.append("- None")
    else:
        for entry in report["safe_replacements"]:
            lines.append(f"- {entry['token']}: replace with {entry['replacement']} ({entry['count']})")
            for sample in entry["samples"]:
                lines.append(f"  sample: {sample}")

    lines.extend(["", "## Ambiguous Shorthand", ""])
    if not report["ambiguous_shorthand"]:
        lines.append("- None")
    else:
        for entry in report["ambiguous_shorthand"]:
            candidates = ", ".join(entry.get("candidates", [])) or "manual review needed"
            lines.append(f"- {entry['token']}: ambiguous among {candidates} ({entry['count']})")
            for sample in entry["samples"]:
                lines.append(f"  sample: {sample}")

    lines.extend(["", "## Unknown Bare Tokens", ""])
    if not report["unknown_tokens"]:
        lines.append("- None")
    else:
        for entry in report["unknown_tokens"]:
            lines.append(f"- {entry['token']}: {entry['count']}")
            for sample in entry["samples"]:
                lines.append(f"  sample: {sample}")

    lines.extend(["", "## Standard-Format But Unmodeled Alleles", ""])
    if not report["unmodeled_standard_alleles"]:
        lines.append("- None")
    else:
        for entry in report["unmodeled_standard_alleles"]:
            lines.append(f"- {entry['token']}: {entry['count']}")
            for sample in entry["samples"]:
                lines.append(f"  sample: {sample}")

    return "\n".join(lines).rstrip() + "\n"


def audit_bloomington_csv(csv_path, data_dir=None, cache_path=None):
    package_counter = Counter()
    unresolved_token_counter = Counter()
    marker_counter = Counter()
    balancer_counter = Counter()
    construct_marker_counter = Counter()
    sample_resolutions = []

    total_packages = 0
    parsed_packages = 0
    fully_resolved_packages = 0

    for package in _iter_bloomington_packages(csv_path):
        total_packages += 1
        package_counter[package] += 1

        parsed = parse_gene_package(package)
        resolved = resolve_package_markers(package)
        if parsed["raw_tokens"]:
            parsed_packages += 1
        if not resolved["unresolved_tokens"]:
            fully_resolved_packages += 1

        unresolved_token_counter.update(resolved["unresolved_tokens"])
        for balancer in parsed["balancers"]:
            balancer_counter[balancer["symbol"]] += 1
        for marker in resolved["markers"]:
            label = marker.get("display_label", marker.get("gene_stem", "?"))
            marker_counter[label] += 1
            if marker.get("source") == "construct_marker":
                construct_marker_counter[label] += 1

        if len(sample_resolutions) < 10:
            sample_resolutions.append(
                {
                    "package": package,
                    "markers": [
                        marker.get("display_label", marker.get("gene_stem", "?"))
                        for marker in resolved["markers"]
                    ],
                    "unresolved_tokens": resolved["unresolved_tokens"],
                }
            )

    unique_packages = len(package_counter)
    top_packages = package_counter.most_common(15)
    top_markers = marker_counter.most_common(15)
    top_unresolved = unresolved_token_counter.most_common(20)
    ambiguous_unresolved_counter, unknown_unresolved_counter = _split_unresolved_token_counts(
        unresolved_token_counter,
        data_dir=data_dir,
        cache_path=cache_path,
    )

    return {
        "csv_path": str(csv_path),
        "total_packages": total_packages,
        "unique_packages": unique_packages,
        "parsed_packages": parsed_packages,
        "fully_resolved_packages": fully_resolved_packages,
        "top_packages": top_packages,
        "top_markers": top_markers,
        "top_balancers": balancer_counter.most_common(10),
        "top_construct_markers": construct_marker_counter.most_common(10),
        "top_unresolved_tokens": top_unresolved,
        "ambiguous_unresolved_total": sum(ambiguous_unresolved_counter.values()),
        "ambiguous_unresolved_unique": len(ambiguous_unresolved_counter),
        "unknown_unresolved_total": sum(unknown_unresolved_counter.values()),
        "unknown_unresolved_unique": len(unknown_unresolved_counter),
        "top_ambiguous_unresolved_tokens": ambiguous_unresolved_counter.most_common(20),
        "top_unknown_unresolved_tokens": unknown_unresolved_counter.most_common(20),
        "sample_resolutions": sample_resolutions,
    }


def render_bloomington_audit_markdown(audit):
    lines = [
        "# Bloomington Parser Audit",
        "",
        f"- CSV path: {audit['csv_path']}",
        f"- Total parsed packages examined: {audit['total_packages']}",
        f"- Unique packages: {audit['unique_packages']}",
        f"- Packages with tokenization output: {audit['parsed_packages']}",
        f"- Packages with no unresolved tokens: {audit['fully_resolved_packages']}",
        "",
        "## Top Packages",
        "",
    ]

    for package, count in audit["top_packages"]:
        lines.append(f"- {package}: {count}")

    lines.extend(["", "## Top Extracted Markers", ""])
    for marker, count in audit["top_markers"]:
        lines.append(f"- {marker}: {count}")

    lines.extend(["", "## Top Balancers", ""])
    for balancer, count in audit["top_balancers"]:
        lines.append(f"- {balancer}: {count}")

    lines.extend(["", "## Top Construct Markers", ""])
    for marker, count in audit["top_construct_markers"]:
        lines.append(f"- {marker}: {count}")

    lines.extend(["", "## Most Frequent Unresolved Tokens", ""])
    if not audit["top_unresolved_tokens"]:
        lines.append("- None")
    else:
        for token, count in audit["top_unresolved_tokens"]:
            lines.append(f"- {token}: {count}")

    lines.extend(["", "## Ambiguous Unresolved Tokens", ""])
    if not audit["top_ambiguous_unresolved_tokens"]:
        lines.append("- None")
    else:
        for token, count in audit["top_ambiguous_unresolved_tokens"]:
            lines.append(f"- {token}: {count}")

    lines.extend(["", "## Unknown Unresolved Tokens", ""])
    if not audit["top_unknown_unresolved_tokens"]:
        lines.append("- None")
    else:
        for token, count in audit["top_unknown_unresolved_tokens"]:
            lines.append(f"- {token}: {count}")

    lines.extend(["", "## Sample Package Resolutions", ""])
    for sample in audit["sample_resolutions"]:
        marker_summary = ", ".join(sample["markers"]) if sample["markers"] else "None"
        unresolved_summary = ", ".join(sample["unresolved_tokens"]) if sample["unresolved_tokens"] else "None"
        lines.append(f"- {sample['package']}")
        lines.append(f"  markers: {marker_summary}")
        lines.append(f"  unresolved: {unresolved_summary}")

    return "\n".join(lines) + "\n"


def render_unresolved_token_curation_markdown(audit):
    lines = [
        "# Unresolved Token Curation Report",
        "",
        f"- CSV path: {audit['csv_path']}",
        f"- Total parsed packages examined: {audit['total_packages']}",
        f"- Packages with no unresolved tokens: {audit['fully_resolved_packages']}",
        f"- Ambiguous unresolved token occurrences: {audit['ambiguous_unresolved_total']}",
        f"- Ambiguous unresolved unique tokens: {audit['ambiguous_unresolved_unique']}",
        f"- Unknown unresolved token occurrences: {audit['unknown_unresolved_total']}",
        f"- Unknown unresolved unique tokens: {audit['unknown_unresolved_unique']}",
        "",
        "## Top Ambiguous Tokens",
        "",
    ]

    if not audit["top_ambiguous_unresolved_tokens"]:
        lines.append("- None")
    else:
        for token, count in audit["top_ambiguous_unresolved_tokens"]:
            lines.append(f"- {token}: {count}")

    lines.extend(["", "## Top Unknown Tokens", ""])
    if not audit["top_unknown_unresolved_tokens"]:
        lines.append("- None")
    else:
        for token, count in audit["top_unknown_unresolved_tokens"]:
            lines.append(f"- {token}: {count}")

    return "\n".join(lines) + "\n"


def render_flybase_examination_markdown(examination):
    lines = [
        "# FlyBase Examination Report",
        "",
        f"- Data directory: {examination['data_dir']}",
        "",
        "## File Summary",
        "",
    ]

    for key, summary in examination["files"].items():
        lines.append(f"### {key}")
        if not summary.get("exists"):
            lines.extend(["", "File not found.", ""])
            continue
        lines.extend(
            [
                "",
                f"- Rows: {summary['row_count']}",
                f"- Columns: {summary['column_count']}",
                f"- Size bytes: {summary['size_bytes']}",
                f"- Column names: {', '.join(summary['columns'])}",
                "",
            ]
        )

    lines.extend(["## Critical Marker Coverage", ""])
    if not examination["marker_coverage"]:
        lines.append("No phenotype table available yet.")
    else:
        for marker, stats in examination["marker_coverage"].items():
            lines.append(
                f"- {marker}: total={stats['total_rows']}, visible={stats['visible_rows']}, anatomy={stats['anatomy_rows']}, unconditional={stats['unconditional_rows']}"
            )

    lines.extend(["", "## Qualifier Examples", ""])
    if not examination["qualifier_examples"]:
        lines.append("No visible phenotype examples collected.")
    else:
        for row in examination["qualifier_examples"]:
            lines.append(f"- {' | '.join(row)}")

    return "\n".join(lines) + "\n"


def render_visible_marker_inventory_markdown(report):
    summary = report["summary"]
    lines = [
        "# Visible Marker Inventory",
        "",
        f"- Phenotype file: {report['phenotype_file_path']}",
        f"- Compatible stock source: {report['bloomington_csv_path']}",
        f"- Visible rows examined: {summary['visible_rows']}",
        f"- Unconditional visible rows: {summary['unconditional_visible_rows']}",
        f"- Visible allele tokens: {summary['visible_alleles']}",
        f"- Visible gene stems: {summary['visible_gene_stems']}",
        f"- Compatible visible allele tokens: {summary['compatible_visible_alleles']}",
        f"- Compatible visible gene stems: {summary['compatible_visible_gene_stems']}",
        f"- High-priority curation stems: {summary['high_priority_gene_stems']}",
        f"- Medium-priority curation stems: {summary['medium_priority_gene_stems']}",
        f"- Low-priority stems outside current compatible stock usage: {summary['low_priority_gene_stems']}",
        "",
        "## Top Compatible Gene Stems",
        "",
    ]

    for row in report["top_gene_stems"][:20]:
        lines.append(
            "- {gene}: compatible={compatible}, visible_rows={visible}, alleles={alleles}, bucket={bucket}, dominance={dominance}, anatomy={anatomy}".format(
                gene=row["gene_stem"],
                compatible=row["compatible_gene_occurrences"],
                visible=row["visible_rows"],
                alleles=row["allele_count"],
                bucket=row["curation_bucket"],
                dominance=", ".join(row["dominance_terms"]) or "n/a",
                anatomy=", ".join(row["body_part_ids"]) or "n/a",
            )
        )

    lines.extend(["", "## Top Compatible Alleles", ""])
    for row in report["top_alleles"][:20]:
        lines.append(
            "- {token}: compatible_allele_occurrences={allele_count}, compatible_gene_occurrences={gene_count}, visible_rows={visible}, dominance={dominance}, anatomy={anatomy}".format(
                token=row["token"],
                allele_count=row["compatible_allele_occurrences"],
                gene_count=row["compatible_gene_occurrences"],
                visible=row["visible_rows"],
                dominance=", ".join(row["dominance_terms"]) or "n/a",
                anatomy=", ".join(row["body_part_ids"]) or "n/a",
            )
        )

    lines.extend(["", "## Dominance Terms", ""])
    if not report["dominance_terms"]:
        lines.append("- None")
    else:
        for term, count in report["dominance_terms"]:
            lines.append(f"- {term}: {count}")

    lines.extend(["", "## Body Part IDs", ""])
    if not report["body_part_ids"]:
        lines.append("- None")
    else:
        for term, count in report["body_part_ids"]:
            lines.append(f"- {term}: {count}")

    lines.extend(["", "## Curation Queue", ""])
    for bucket, label in (("high", "High Priority"), ("medium", "Medium Priority"), ("low", "Low Priority")):
        lines.extend([f"### {label}", ""])
        queue = report["curation_queue"][bucket]
        if not queue:
            lines.append("- None")
            lines.append("")
            continue
        for row in queue[:30]:
            lines.append(
                "- {gene}: compatible={compatible}, alleles={alleles}, dominance={dominance}, anatomy={anatomy}".format(
                    gene=row["gene_stem"],
                    compatible=row["compatible_gene_occurrences"],
                    alleles=", ".join(row["alleles"][:5]),
                    dominance=", ".join(row["dominance_terms"]) or "n/a",
                    anatomy=", ".join(row["body_part_ids"]) or "n/a",
                )
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def summarize_genotype(genotype, sex):
    return compute_marker_phenotype(genotype, sex)
