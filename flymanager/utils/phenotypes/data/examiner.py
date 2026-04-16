import csv
import gzip
import re
from collections import Counter
from pathlib import Path

from flymanager.utils.phenotypes.compute import compute_marker_phenotype
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.resolver import resolve_package_markers
from flymanager.utils.phenotypes.visual_markers import CRITICAL_MARKERS

EXPECTED_FLYBASE_FILES = {
    "genotype_phenotype_data": "genotype_phenotype_data",
    "fbal_to_fbgn": "fbal_to_fbgn",
    "allele_descriptions": "dmel_classical_and_insertion_allele_descriptions",
    "stocks": "stocks",
    "construct_descriptions": "transgenic_construct_descriptions",
    "split_system_combinations": "split_system_combinations",
    "gene_map_table": "gene_map_table",
}


def _find_matching_file(data_dir, prefix):
    data_dir = Path(data_dir)
    matches = sorted(
        path
        for pattern in (f"{prefix}*.tsv", f"{prefix}*.tsv.gz")
        for path in data_dir.glob(pattern)
    )
    return matches[0] if matches else None


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


def audit_bloomington_csv(csv_path):
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

    lines.extend(["", "## Sample Package Resolutions", ""])
    for sample in audit["sample_resolutions"]:
        marker_summary = ", ".join(sample["markers"]) if sample["markers"] else "None"
        unresolved_summary = ", ".join(sample["unresolved_tokens"]) if sample["unresolved_tokens"] else "None"
        lines.append(f"- {sample['package']}")
        lines.append(f"  markers: {marker_summary}")
        lines.append(f"  unresolved: {unresolved_summary}")

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


def summarize_genotype(genotype, sex):
    return compute_marker_phenotype(genotype, sex)
