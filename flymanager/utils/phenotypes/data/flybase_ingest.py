import csv
import gzip
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

from flymanager.utils.phenotypes.flybase_pipeline import (
    lookup_flybase_allele_marker, lookup_flybase_marker_alias)
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.resolver import resolve_package_markers
from flymanager.utils.phenotypes.visual_markers import (
    get_reviewed_marker_alias, get_visual_marker)

FLYBASE_PREFIXES = {
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


def _normalize_text(value):
    return str(value or "").strip()


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
    if not header[0].strip() and len(header) > 1:
        header = header[1:]
    return header


def _iter_tsv_rows(file_path):
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
            if len(row) < len(headers) and headers and headers[0] == "":
                row = row[1:]
            yield row, header_index


def _find_matching_file(data_dir, prefix):
    data_dir = Path(data_dir)
    matches = sorted(
        path
        for pattern in (f"{prefix}*.tsv", f"{prefix}*.tsv.gz")
        for path in data_dir.glob(pattern)
    )
    return matches[0] if matches else None


def _split_multi_value_field(value):
    text = _normalize_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"\s*[|;]\s*", text) if item.strip()]


def _safe_int(value, default=0):
    text = _normalize_text(value)
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _extract_dominance_terms(*texts):
    terms = set()
    for text in texts:
        for match in DOMINANCE_TERM_RE.findall(str(text or "")):
            normalized = match.lower().replace("semidominant", "semi-dominant")
            terms.add(normalized)
    return sorted(terms)


def _extract_body_part_ids(*texts):
    body_part_ids = set()
    for text in texts:
        body_part_ids.update(match.upper() for match in ANATOMY_TERM_RE.findall(str(text or "")))
    return sorted(body_part_ids)


def _extract_allele_tokens(genotype_text):
    tokens = []
    for match in ALLELE_TOKEN_RE.finditer(str(genotype_text or "")):
        tokens.append(
            {
                "token": match.group(0),
                "gene_stem": match.group("gene"),
                "allele_spec": match.group("allele"),
            }
        )
    return tokens


def _replace_collection(collection, docs):
    collection.delete_many({})
    if docs:
        collection.insert_many(docs)


def _default_collection_name(name):
    return {
        "genotype_phenotype_data": "flybase_phenotypes",
        "fbal_to_fbgn": "flybase_allele_genes",
        "stocks": "flybase_stock_alleles",
        "allele_descriptions": "flybase_allele_descriptions",
        "construct_descriptions": "flybase_constructs",
        "split_system_combinations": "flybase_split_combinations",
        "gene_map_table": "flybase_gene_map",
    }[name]


def ingest_genotype_phenotype_data(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("genotype_phenotype_data")
    docs = []
    summary = Counter()

    for row, header_index in _iter_tsv_rows(filepath):
        genotype_symbols = row[header_index.get("genotype_symbols", 0)]
        genotype_fbids = row[header_index.get("genotype_FBids", 1)] if len(row) > 1 else ""
        phenotype_name = row[header_index.get("phenotype_name", 2)] if len(row) > 2 else ""
        phenotype_id = row[header_index.get("phenotype_id", 3)] if len(row) > 3 else ""
        qualifier_names = row[header_index.get("qualifier_names", 4)] if len(row) > 4 else ""
        qualifier_ids = row[header_index.get("qualifier_ids", 5)] if len(row) > 5 else ""
        reference = row[header_index.get("reference", 6)] if len(row) > 6 else ""
        allele_tokens = _extract_allele_tokens(genotype_symbols)

        is_visible = "visible" in _normalize_text(phenotype_name).lower()
        analysis_text = " ".join([
            _normalize_text(phenotype_name).lower(),
            _normalize_text(qualifier_names).lower(),
            _normalize_text(qualifier_ids).lower(),
        ])
        is_unconditional_visible = is_visible and "with genotype" not in analysis_text and " with " not in analysis_text

        docs.append(
            {
                "genotype_symbols": _normalize_text(genotype_symbols),
                "genotype_fbids": _split_multi_value_field(genotype_fbids),
                "phenotype_name": _normalize_text(phenotype_name),
                "phenotype_id": _normalize_text(phenotype_id),
                "qualifier_names": _split_multi_value_field(qualifier_names),
                "qualifier_ids": _split_multi_value_field(qualifier_ids),
                "reference": _normalize_text(reference),
                "allele_tokens": [token["token"] for token in allele_tokens],
                "gene_symbols": sorted({token["gene_stem"] for token in allele_tokens}),
                "dominance_terms": _extract_dominance_terms(phenotype_name, qualifier_names, qualifier_ids),
                "body_part_ids": _extract_body_part_ids(phenotype_name, qualifier_names, qualifier_ids),
                "is_visible": is_visible,
                "is_unconditional_visible": is_unconditional_visible,
                "source_file": str(filepath),
            }
        )
        summary["total_rows"] += 1
        if is_visible:
            summary["visible_rows"] += 1
        if is_unconditional_visible:
            summary["unconditional_visible_rows"] += 1

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("allele_tokens")
    collection.create_index("gene_symbols")
    collection.create_index([("gene_symbols", 1), ("is_unconditional_visible", 1)])
    return {
        "collection": collection_name,
        "inserted": len(docs),
        **summary,
    }


def ingest_fbal_to_fbgn(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("fbal_to_fbgn")
    docs = []

    for row, header_index in _iter_tsv_rows(filepath):
        docs.append(
            {
                "allele_id": _normalize_text(row[header_index.get("AlleleID", 0)]),
                "allele_symbol": _normalize_text(row[header_index.get("AlleleSymbol", 1)]),
                "gene_id": _normalize_text(row[header_index.get("GeneID", 2)]),
                "gene_symbol": _normalize_text(row[header_index.get("GeneSymbol", 3)]),
                "source_file": str(filepath),
            }
        )

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("allele_id")
    collection.create_index("allele_symbol")
    collection.create_index("gene_symbol")
    return {
        "collection": collection_name,
        "inserted": len(docs),
    }


def ingest_stocks(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("stocks")
    docs = []
    species_counter = Counter()

    for row, header_index in _iter_tsv_rows(filepath):
        species = _normalize_text(row[header_index.get("species", 3)])
        species_counter[species or "unknown"] += 1
        docs.append(
            {
                "FBst": _normalize_text(row[header_index.get("FBst", 0)]),
                "collection_short_name": _normalize_text(row[header_index.get("collection_short_name", 1)]),
                "stock_type_cv": _normalize_text(row[header_index.get("stock_type_cv", 2)]),
                "species": species,
                "FB_genotype": _normalize_text(row[header_index.get("FB_genotype", 4)]),
                "description": _normalize_text(row[header_index.get("description", 5)]),
                "stock_number": _normalize_text(row[header_index.get("stock_number", 6)]),
                "source_file": str(filepath),
            }
        )

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("FBst")
    collection.create_index("stock_number")
    collection.create_index([("collection_short_name", 1), ("stock_number", 1)])
    return {
        "collection": collection_name,
        "inserted": len(docs),
        "species_counts": dict(species_counter),
    }


def ingest_allele_descriptions(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("allele_descriptions")
    docs = []

    for row, header_index in _iter_tsv_rows(filepath):
        docs.append(
            {
                "allele_symbol": _normalize_text(row[header_index.get("Allele (symbol)", 0)]),
                "allele_id": _normalize_text(row[header_index.get("Allele (id)", 1)]),
                "gene_symbol": _normalize_text(row[header_index.get("Gene (symbol)", 2)]),
                "gene_id": _normalize_text(row[header_index.get("Gene (id)", 3)]),
                "allele_class_term": _normalize_text(row[header_index.get("Allele Class (term)", 4)]),
                "allele_class_id": _normalize_text(row[header_index.get("Allele Class (id)", 5)]),
                "insertion_symbol": _normalize_text(row[header_index.get("Insertion (symbol)", 6)]),
                "insertion_id": _normalize_text(row[header_index.get("Insertion (id)", 7)]),
                "regulatory_region_symbol": _normalize_text(row[header_index.get("Regulatory region (symbol)", 10)]),
                "regulatory_region_id": _normalize_text(row[header_index.get("Regulatory region (id)", 11)]),
                "encoded_product_symbol": _normalize_text(row[header_index.get("Encoded product/tool (symbol)", 12)]),
                "encoded_product_id": _normalize_text(row[header_index.get("Encoded product/tool (id)", 13)]),
                "description": _normalize_text(row[header_index.get("Description (text)", 18)]),
                "description_reference": _normalize_text(row[header_index.get("Description (supporting reference)", 19)]),
                "stocks_number": _safe_int(row[header_index.get("Stocks (number)", 20)] if len(row) > 20 else 0),
                "source_file": str(filepath),
            }
        )

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("allele_symbol")
    collection.create_index("gene_symbol")
    return {
        "collection": collection_name,
        "inserted": len(docs),
    }


def ingest_construct_descriptions(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("construct_descriptions")
    docs = []

    for row, header_index in _iter_tsv_rows(filepath):
        docs.append(
            {
                "component_allele_symbol": _normalize_text(row[header_index.get("Component Allele (symbol)", 0)]),
                "component_allele_id": _normalize_text(row[header_index.get("Component Allele (id)", 1)]),
                "construct_symbol": _normalize_text(row[header_index.get("Transgenic Construct (symbol)", 2)]),
                "construct_id": _normalize_text(row[header_index.get("Transgenic Construct (id)", 3)]),
                "product_class_term": _normalize_text(row[header_index.get("Transgenic Product class (term)", 4)]),
                "product_class_id": _normalize_text(row[header_index.get("Transgenic Product class (id)", 5)]),
                "regulatory_region_symbol": _normalize_text(row[header_index.get("Regulatory region (symbol)", 6)]),
                "regulatory_region_id": _normalize_text(row[header_index.get("Regulatory region (id)", 7)]),
                "encoded_product_symbol": _normalize_text(row[header_index.get("Encoded product/tool (symbol)", 8)]),
                "encoded_product_id": _normalize_text(row[header_index.get("Encoded product/tool (id)", 9)]),
                "tagged_symbol": _normalize_text(row[header_index.get("Tagged with (symbol)", 10)]),
                "tagged_id": _normalize_text(row[header_index.get("Tagged with (id)", 11)]),
                "also_carries_symbol": _normalize_text(row[header_index.get("Also carries (symbol)", 12)]),
                "also_carries_id": _normalize_text(row[header_index.get("Also carries (id)", 13)]),
                "description": _normalize_text(row[header_index.get("Description (text)", 14)]),
                "description_reference": _normalize_text(row[header_index.get("Description (supporting reference)", 15)]),
                "stocks_number": _safe_int(row[header_index.get("Stocks (number)", 16)] if len(row) > 16 else 0),
                "source_file": str(filepath),
            }
        )

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("construct_symbol")
    collection.create_index("component_allele_symbol")
    return {
        "collection": collection_name,
        "inserted": len(docs),
    }


def ingest_split_system_combinations(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("split_system_combinations")
    docs = []

    for row, header_index in _iter_tsv_rows(filepath):
        docs.append(
            {
                "symbol": _normalize_text(row[header_index.get("Symbol", 0)]),
                "component_alleles": _split_multi_value_field(row[header_index.get("Component_Alleles", 1)] if len(row) > 1 else ""),
                "stocks": _split_multi_value_field(row[header_index.get("Stocks", 2)] if len(row) > 2 else ""),
                "references": _split_multi_value_field(row[header_index.get("References", 3)] if len(row) > 3 else ""),
                "source_file": str(filepath),
            }
        )

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("symbol")
    return {
        "collection": collection_name,
        "inserted": len(docs),
    }


def ingest_gene_map_table(filepath, db, collection_name=None):
    collection_name = collection_name or _default_collection_name("gene_map_table")
    docs = []

    for row, header_index in _iter_tsv_rows(filepath):
        docs.append(
            {
                "organism_abbreviation": _normalize_text(row[header_index.get("organism_abbreviation", 0)]),
                "current_symbol": _normalize_text(row[header_index.get("current_symbol", 1)]),
                "primary_FBid": _normalize_text(row[header_index.get("primary_FBid", 2)]),
                "recombination_loc": _normalize_text(row[header_index.get("recombination_loc", 3)]),
                "cytogenetic_loc": _normalize_text(row[header_index.get("cytogenetic_loc", 4)]),
                "sequence_loc": _normalize_text(row[header_index.get("sequence_loc", 5)]),
                "source_file": str(filepath),
            }
        )

    collection = db[collection_name]
    _replace_collection(collection, docs)
    collection.create_index("current_symbol")
    collection.create_index("primary_FBid")
    return {
        "collection": collection_name,
        "inserted": len(docs),
    }


def ingest_flybase_bundle(data_dir, db):
    data_dir = Path(data_dir)
    source_files = {
        key: _find_matching_file(data_dir, prefix)
        for key, prefix in FLYBASE_PREFIXES.items()
    }
    missing = [key for key, path in source_files.items() if path is None]
    if missing:
        raise FileNotFoundError(f"Missing FlyBase input files for: {', '.join(missing)}")

    report = {
        "data_dir": str(data_dir),
        "source_files": {key: str(path) for key, path in source_files.items()},
        "collections": {},
    }
    report["collections"]["flybase_phenotypes"] = ingest_genotype_phenotype_data(source_files["genotype_phenotype_data"], db)
    report["collections"]["flybase_allele_genes"] = ingest_fbal_to_fbgn(source_files["fbal_to_fbgn"], db)
    report["collections"]["flybase_stock_alleles"] = ingest_stocks(source_files["stocks"], db)
    report["collections"]["flybase_allele_descriptions"] = ingest_allele_descriptions(source_files["allele_descriptions"], db)
    report["collections"]["flybase_constructs"] = ingest_construct_descriptions(source_files["construct_descriptions"], db)
    report["collections"]["flybase_split_combinations"] = ingest_split_system_combinations(source_files["split_system_combinations"], db)
    report["collections"]["flybase_gene_map"] = ingest_gene_map_table(source_files["gene_map_table"], db)
    return report


def inspect_allele_resolution(token, db=None):
    normalized = _normalize_text(token)
    allele_match = ALLELE_TOKEN_RE.fullmatch(normalized)
    gene_stem = allele_match.group("gene") if allele_match else normalized
    allele_spec = allele_match.group("allele") if allele_match else ""

    manual_marker = get_visual_marker(gene_stem, allele_spec=allele_spec or None, token=normalized)
    reviewed_alias = get_reviewed_marker_alias(normalized)
    flybase_marker = lookup_flybase_allele_marker(
        normalized,
        gene_stem=gene_stem,
        allele_spec=allele_spec or None,
        db=db,
    )
    flybase_alias = lookup_flybase_marker_alias(normalized, db=db)

    db_matches = {}
    if db is not None:
        db_matches = {
            "allele_gene": deepcopy(db["flybase_allele_genes"].find_one({"allele_symbol": normalized}) or {}),
            "allele_description": deepcopy(db["flybase_allele_descriptions"].find_one({"allele_symbol": normalized}) or {}),
            "phenotype_count": db["flybase_phenotypes"].count_documents({"allele_tokens": normalized}),
        }

    return {
        "token": normalized,
        "manual_marker": manual_marker,
        "reviewed_alias": reviewed_alias,
        "flybase_marker": flybase_marker,
        "flybase_alias": flybase_alias,
        "db_matches": db_matches,
    }


def audit_live_stock_resolution(db, collection_name="stocks"):
    collection = db[collection_name]
    summary = Counter()
    unresolved_tokens = Counter()
    marker_sources = Counter()
    top_balancers = Counter()
    top_constructs = Counter()
    sample_unresolved = []

    for record in collection.find({}, {"UniqueID": 1, "User": 1, "Name": 1, "Genotype": 1}):
        summary["records_scanned"] += 1
        genotype = _normalize_text(record.get("Genotype"))
        if not genotype:
            summary["missing_genotype"] += 1
            continue

        parsed = parse_gene_package(genotype)
        resolved = resolve_package_markers(genotype, db=db)

        if parsed.get("balancers"):
            summary["records_with_balancers"] += 1
            for balancer in parsed["balancers"]:
                top_balancers[balancer.get("symbol", "unknown")] += 1
        if parsed.get("constructs"):
            summary["records_with_constructs"] += 1
            for construct in parsed["constructs"]:
                top_constructs[construct.get("full", "construct")] += 1
        if parsed.get("classical_alleles"):
            summary["records_with_classical_alleles"] += 1

        if resolved.get("unresolved_tokens"):
            summary["records_with_unresolved_tokens"] += 1
            for token in resolved["unresolved_tokens"]:
                unresolved_tokens[token] += 1
            if len(sample_unresolved) < 10:
                sample_unresolved.append(
                    {
                        "unique_id": _normalize_text(record.get("UniqueID")),
                        "name": _normalize_text(record.get("Name")),
                        "genotype": genotype,
                        "unresolved_tokens": list(resolved["unresolved_tokens"]),
                    }
                )
        else:
            summary["fully_resolved_records"] += 1

        for marker in resolved.get("markers", []):
            marker_sources[marker.get("source", "unknown")] += 1

    return {
        "collection": collection_name,
        "summary": dict(summary),
        "top_unresolved_tokens": unresolved_tokens.most_common(25),
        "marker_sources": marker_sources.most_common(10),
        "top_balancers": top_balancers.most_common(10),
        "top_constructs": top_constructs.most_common(10),
        "sample_unresolved_records": sample_unresolved,
    }


def render_live_stock_resolution_markdown(report):
    summary = report["summary"]
    lines = [
        "# Live Stock Resolution Report",
        "",
        f"- Collection: {report['collection']}",
        f"- Records scanned: {summary.get('records_scanned', 0)}",
        f"- Fully resolved records: {summary.get('fully_resolved_records', 0)}",
        f"- Records with unresolved tokens: {summary.get('records_with_unresolved_tokens', 0)}",
        f"- Records with balancers: {summary.get('records_with_balancers', 0)}",
        f"- Records with constructs: {summary.get('records_with_constructs', 0)}",
        f"- Records with classical alleles: {summary.get('records_with_classical_alleles', 0)}",
        "",
        "## Marker Sources",
        "",
    ]

    if report["marker_sources"]:
        for source, count in report["marker_sources"]:
            lines.append(f"- {source}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "## Top Balancers", ""])
    if report["top_balancers"]:
        for symbol, count in report["top_balancers"]:
            lines.append(f"- {symbol}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "## Top Unresolved Tokens", ""])
    if report["top_unresolved_tokens"]:
        for token, count in report["top_unresolved_tokens"]:
            lines.append(f"- {token}: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "## Sample Unresolved Records", ""])
    if report["sample_unresolved_records"]:
        for sample in report["sample_unresolved_records"]:
            lines.append(f"- {sample['unique_id']} {sample['name']}")
            lines.append(f"  genotype: {sample['genotype']}")
            lines.append(f"  unresolved: {', '.join(sample['unresolved_tokens'])}")
    else:
        lines.append("- None")

    return "\n".join(lines).rstrip() + "\n"