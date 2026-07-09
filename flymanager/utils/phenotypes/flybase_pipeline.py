import csv
import gzip
import json
import os
import re
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from flymanager.utils.genetics import qc_genotype
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

PHENOTYPE_EVIDENCE_CACHE_VERSION = 3
PHENOTYPE_PIPELINE_SIGNATURE_VERSION = 3
DEFAULT_CACHE_FILENAME = "PHENOTYPE_EVIDENCE_CACHE.json"

SOURCE_PREFIXES = {
    "genotype_phenotype_data": "genotype_phenotype_data",
    "allele_descriptions": "dmel_classical_and_insertion_allele_descriptions",
    "construct_descriptions": "transgenic_construct_descriptions",
    "split_system_combinations": "split_system_combinations",
    "stocks": "stocks",
}

ALLELE_TOKEN_RE = re.compile(r"(?P<gene>[A-Za-z0-9.+*_-]+)\[(?P<allele>[^\]]+)\]")
CONSTRUCT_TOKEN_RE = re.compile(r"(?P<token>(?:P|PBac|Mi|TI|M)\{[^}]+\})")
ANATOMY_TERM_RE = re.compile(r"FBbt:\d+", re.IGNORECASE)
DOMINANCE_TERM_RE = re.compile(
    r"\b(dominant|recessive|semi-dominant|semidominant|codominant|maternal effect)\b",
    re.IGNORECASE,
)
DRIVER_KEYWORDS = ("GAL4", "LexA", "QF", "FLP", "FLPo", "Cre")
REPORTER_KEYWORDS = ("GFP", "YFP", "RFP", "mCherry", "tdTomato", "lacZ", "HA")
CONSEQUENCE_PATTERNS = {
    "lethality": re.compile(r"\b(lethal|lethality|inviable|semi-lethal|semilethal)\b", re.IGNORECASE),
    "viability": re.compile(r"\b(viable|viability|survival)\b", re.IGNORECASE),
    "sterility": re.compile(r"\b(sterile|sterility|infertile)\b", re.IGNORECASE),
    "fertility": re.compile(r"\b(fertile|fertility)\b", re.IGNORECASE),
}
STAGE_PATTERNS = {
    "embryonic": re.compile(r"\b(embryo|embryonic)\b", re.IGNORECASE),
    "larval": re.compile(r"\b(larva|larval|larvae)\b", re.IGNORECASE),
    "pupal": re.compile(r"\b(pupa|pupal)\b", re.IGNORECASE),
    "adult": re.compile(r"\b(adult)\b", re.IGNORECASE),
}
CONTEXT_PATTERNS = {
    "conditional": re.compile(r"\b(with genotype|with |temperature-sensitive|heat-sensitive|cold-sensitive|somatic clone|mosaic)\b", re.IGNORECASE),
    "male_specific": re.compile(r"\b(male|males)\b", re.IGNORECASE),
    "female_specific": re.compile(r"\b(female|females)\b", re.IGNORECASE),
}

_IN_MEMORY_CACHE = {}


def _repo_root():
    return Path(__file__).resolve().parents[3]


def resolve_flybase_data_dir(data_dir=None):
    if data_dir is not None:
        return Path(data_dir)

    env_dir = os.environ.get("FLYMANAGER_FLYBASE_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir)

    return _repo_root() / "data" / "flybase"


def resolve_flybase_phenotype_cache_path(data_dir=None, cache_path=None):
    if cache_path is not None:
        return Path(cache_path)

    env_path = os.environ.get("FLYMANAGER_FLYBASE_CACHE_PATH", "").strip()
    if env_path:
        return Path(env_path)

    return resolve_flybase_data_dir(data_dir) / DEFAULT_CACHE_FILENAME


def _timestamp():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _find_matching_file(data_dir, prefix):
    matches = sorted(
        path
        for pattern in (f"{prefix}*.tsv", f"{prefix}*.tsv.gz")
        for path in Path(data_dir).glob(pattern)
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
            yield row, header_index


def _split_multi_value_field(value):
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in re.split(r"\s*[|;]\s*", text) if item.strip()]


def _split_pipe_field(value):
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.split("|") if item.strip()]


def _normalize_alias_key(value):
    return str(value or "").strip().lower()


def _is_safe_alias_candidate(value):
    text = str(value or "").strip()
    if len(text) < 2 or len(text) > 24:
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9.+_-]*$", text))


def _normalize_construct_token(construct_token):
    token = str(construct_token or "").strip()
    if not token:
        return {"full": "", "prefix": "", "body": ""}

    match = CONSTRUCT_TOKEN_RE.search(token)
    if not match:
        return {"full": token, "prefix": token, "body": token}

    prefix = match.group("token")
    body = prefix[prefix.index("{") + 1:-1]
    return {"full": token, "prefix": prefix, "body": body}


def _extract_dominance_terms(*texts):
    terms = set()
    for text in texts:
        for match in DOMINANCE_TERM_RE.findall(str(text or "")):
            normalized = match.lower().replace("semidominant", "semi-dominant")
            terms.add(normalized)
    return terms


def _extract_body_part_ids(*texts):
    body_parts = set()
    for text in texts:
        body_parts.update(match.upper() for match in ANATOMY_TERM_RE.findall(str(text or "")))
    return body_parts


def _extract_consequence_categories(*texts):
    categories = set()
    for label, pattern in CONSEQUENCE_PATTERNS.items():
        for text in texts:
            if pattern.search(str(text or "")):
                categories.add(label)
                break
    return categories


def _extract_stage_terms(*texts):
    stages = set()
    for label, pattern in STAGE_PATTERNS.items():
        for text in texts:
            if pattern.search(str(text or "")):
                stages.add(label)
                break
    return stages


def _extract_context_terms(*texts):
    contexts = set()
    for label, pattern in CONTEXT_PATTERNS.items():
        for text in texts:
            if pattern.search(str(text or "")):
                contexts.add(label)
                break
    return contexts


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


def _collect_source_files(data_dir):
    files = {}
    for key, prefix in SOURCE_PREFIXES.items():
        files[key] = _find_matching_file(data_dir, prefix)
    return files


def _build_source_fingerprints(data_dir):
    files = _collect_source_files(data_dir)
    fingerprints = {}
    for key, path in files.items():
        if path is None or not Path(path).exists():
            fingerprints[key] = {"path": "", "size": 0, "mtime": 0}
            continue
        stat = Path(path).stat()
        fingerprints[key] = {
            "path": str(path),
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        }
    return fingerprints


def compute_flybase_pipeline_signature(data_dir=None):
    data_dir = resolve_flybase_data_dir(data_dir)
    payload = {
        "version": PHENOTYPE_PIPELINE_SIGNATURE_VERSION,
        "data_dir": str(data_dir),
        "sources": _build_source_fingerprints(data_dir),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _stock_occurrence_indexes(stocks_path):
    allele_counter = Counter()
    gene_counter = Counter()
    construct_counter = Counter()
    normalized_genotype_counter = Counter()

    with _open_text_file(stocks_path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            genotype = str(row.get("FB_genotype", "") or "").strip()
            if not genotype:
                continue

            qc_passed, normalized_or_error = qc_genotype(genotype)
            if qc_passed:
                normalized_genotype_counter[normalized_or_error] += 1

            for match in ALLELE_TOKEN_RE.finditer(genotype):
                token = match.group(0)
                gene = match.group("gene")
                allele_counter[token] += 1
                gene_counter[gene] += 1

            for match in CONSTRUCT_TOKEN_RE.finditer(genotype):
                normalized_construct = _normalize_construct_token(match.group("token"))
                construct_counter[normalized_construct["prefix"]] += 1
                construct_counter[normalized_construct["body"]] += 1

    return {
        "allele_counter": allele_counter,
        "gene_counter": gene_counter,
        "construct_counter": construct_counter,
        "normalized_genotype_counter": normalized_genotype_counter,
    }


def _stock_occurrence_indexes_from_db(db):
    allele_counter = Counter()
    gene_counter = Counter()
    construct_counter = Counter()
    normalized_genotype_counter = Counter()

    for row in db["flybase_stock_alleles"].find({}, {"FB_genotype": 1}):
        genotype = str(row.get("FB_genotype", "") or "").strip()
        if not genotype:
            continue

        qc_passed, normalized_or_error = qc_genotype(genotype)
        if qc_passed:
            normalized_genotype_counter[normalized_or_error] += 1

        for match in ALLELE_TOKEN_RE.finditer(genotype):
            token = match.group(0)
            gene = match.group("gene")
            allele_counter[token] += 1
            gene_counter[gene] += 1

        for match in CONSTRUCT_TOKEN_RE.finditer(genotype):
            normalized_construct = _normalize_construct_token(match.group("token"))
            construct_counter[normalized_construct["prefix"]] += 1
            construct_counter[normalized_construct["body"]] += 1

    return {
        "allele_counter": allele_counter,
        "gene_counter": gene_counter,
        "construct_counter": construct_counter,
        "normalized_genotype_counter": normalized_genotype_counter,
    }


def _derive_marker_record(token, gene_stem, allele_spec, stats):
    curated_marker = get_visual_marker(gene_stem, allele_spec=allele_spec, token=token)
    if curated_marker is not None:
        marker = deepcopy(curated_marker)
    else:
        dominance_terms = set(stats.get("dominance_terms", []))
        dominance = "recessive" if "recessive" in dominance_terms else "dominant"
        marker = {
            "gene_stem": gene_stem,
            "body_part": None,
            "effect": "FlyBase visible phenotype evidence",
            "dominance": dominance,
            "display_label": gene_stem,
            "phenotype_key": f"flybase:{token}",
            "chromosome": None,
            "scoring_confidence": 0.45,
            "source": "flybase_allele_evidence",
        }

    visible_rows = int(stats.get("visible_rows", 0))
    reference_count = int(stats.get("reference_count", 0))
    stock_occurrences = int(stats.get("stock_occurrences", 0))
    confidence_bonus = min(visible_rows, 4) * 0.04 + min(reference_count, 3) * 0.03
    if stock_occurrences > 0:
        confidence_bonus += 0.1

    base_score = float(marker.get("scoring_confidence", 0.55))
    marker.update(
        {
            "token": token,
            "gene_stem": gene_stem,
            "allele_spec": allele_spec,
            "source": "flybase_allele_evidence",
            "flybase_visible_rows": visible_rows,
            "flybase_reference_count": reference_count,
            "flybase_body_part_ids": list(stats.get("body_part_ids", [])),
            "flybase_dominance_terms": list(stats.get("dominance_terms", [])),
            "flybase_stock_occurrences": stock_occurrences,
            "scoring_confidence": round(min(0.98, max(base_score, base_score + confidence_bonus)), 2),
            "flybase_evidence": True,
        }
    )

    dominance_terms = set(stats.get("dominance_terms", []))
    if dominance_terms == {"recessive"}:
        marker["dominance"] = "recessive"
    elif dominance_terms == {"dominant"}:
        marker["dominance"] = "dominant"

    return marker


def _should_promote_allele_marker(record):
    curated_marker = get_visual_marker(record["gene_stem"])
    if curated_marker is not None:
        return True

    dominance_terms = set(record["dominance_terms"])
    has_conflicting_dominance = "dominant" in dominance_terms and "recessive" in dominance_terms
    has_anatomy_specificity = bool(record["body_part_ids"])
    has_stock_support = record["stock_occurrences"] > 0
    return has_stock_support and has_anatomy_specificity and not has_conflicting_dominance


def _build_allele_marker_index(source_files):
    phenotype_path = source_files.get("genotype_phenotype_data")
    stocks_path = source_files.get("stocks")
    empty_stock_indexes = {
        "allele_counter": Counter(),
        "gene_counter": Counter(),
        "construct_counter": Counter(),
        "normalized_genotype_counter": Counter(),
    }

    if phenotype_path is None or stocks_path is None:
        return {
            "allele_markers": {},
            "summary": {"visible_rows": 0, "cached_alleles": 0},
            "stock_indexes": empty_stock_indexes,
        }

    stock_indexes = _stock_occurrence_indexes(stocks_path)
    allele_inventory = {}
    visible_row_count = 0

    for row, header_index in _iter_tsv_rows(phenotype_path):
        phenotype_name = row[header_index.get("phenotype_name", 2)]
        qualifier_names = row[header_index.get("qualifier_names", 4)] if len(row) > 4 else ""
        qualifier_ids = row[header_index.get("qualifier_ids", 5)] if len(row) > 5 else ""
        genotype_symbols = row[header_index.get("genotype_symbols", 0)]
        reference = row[header_index.get("reference", 6)] if len(row) > 6 else ""

        if not _is_unconditional_visible_row(phenotype_name, qualifier_names, qualifier_ids):
            continue

        visible_row_count += 1
        dominance_terms = _extract_dominance_terms(phenotype_name, qualifier_names, qualifier_ids)
        body_part_ids = _extract_body_part_ids(phenotype_name, qualifier_names, qualifier_ids)

        for match in ALLELE_TOKEN_RE.finditer(genotype_symbols or ""):
            token = match.group(0)
            gene_stem = match.group("gene")
            allele_spec = match.group("allele")
            record = allele_inventory.setdefault(
                token,
                {
                    "token": token,
                    "gene_stem": gene_stem,
                    "allele_spec": allele_spec,
                    "visible_rows": 0,
                    "references": set(),
                    "dominance_terms": set(),
                    "body_part_ids": set(),
                    "stock_occurrences": stock_indexes["allele_counter"].get(token, 0),
                    "gene_stock_occurrences": stock_indexes["gene_counter"].get(gene_stem, 0),
                },
            )
            record["visible_rows"] += 1
            if reference:
                record["references"].add(reference)
            record["dominance_terms"].update(dominance_terms)
            record["body_part_ids"].update(body_part_ids)

    allele_markers = {}
    for token, record in allele_inventory.items():
        if not _should_promote_allele_marker(record):
            continue
        allele_markers[token] = _derive_marker_record(
            token,
            record["gene_stem"],
            record["allele_spec"],
            {
                "visible_rows": record["visible_rows"],
                "reference_count": len(record["references"]),
                "dominance_terms": sorted(record["dominance_terms"]),
                "body_part_ids": sorted(record["body_part_ids"]),
                "stock_occurrences": record["stock_occurrences"],
            },
        )

    return {
        "allele_markers": allele_markers,
        "summary": {
            "visible_rows": visible_row_count,
            "cached_alleles": len(allele_markers),
            "stock_alleles": len(stock_indexes["allele_counter"]),
        },
        "stock_indexes": stock_indexes,
    }


def _build_consequence_index(source_files):
    phenotype_path = source_files.get("genotype_phenotype_data")
    if phenotype_path is None:
        return {
            "allele_consequences": {},
            "summary": {"cached_consequence_alleles": 0},
        }

    consequence_inventory = {}
    for row, header_index in _iter_tsv_rows(phenotype_path):
        phenotype_name = row[header_index.get("phenotype_name", 2)] if len(row) > 2 else ""
        qualifier_names = row[header_index.get("qualifier_names", 4)] if len(row) > 4 else ""
        qualifier_ids = row[header_index.get("qualifier_ids", 5)] if len(row) > 5 else ""
        genotype_symbols = row[header_index.get("genotype_symbols", 0)]
        reference = row[header_index.get("reference", 6)] if len(row) > 6 else ""

        categories = _extract_consequence_categories(phenotype_name, qualifier_names, qualifier_ids)
        if not categories:
            continue

        dominance_terms = _extract_dominance_terms(phenotype_name, qualifier_names, qualifier_ids)
        stage_terms = _extract_stage_terms(phenotype_name, qualifier_names, qualifier_ids)
        context_terms = _extract_context_terms(phenotype_name, qualifier_names, qualifier_ids)

        for match in ALLELE_TOKEN_RE.finditer(genotype_symbols or ""):
            token = match.group(0)
            gene_stem = match.group("gene")
            allele_spec = match.group("allele")
            record = consequence_inventory.setdefault(
                token,
                {
                    "token": token,
                    "gene_stem": gene_stem,
                    "allele_spec": allele_spec,
                    "categories": set(),
                    "stage_terms": set(),
                    "context_terms": set(),
                    "dominance_terms": set(),
                    "references": set(),
                    "example_phenotypes": set(),
                    "conditional": False,
                    "source": "flybase_phenotype_consequence",
                },
            )
            record["categories"].update(categories)
            record["stage_terms"].update(stage_terms)
            record["context_terms"].update(context_terms)
            record["dominance_terms"].update(dominance_terms)
            record["conditional"] = record["conditional"] or ("conditional" in context_terms)
            if reference:
                record["references"].add(reference)
            if phenotype_name:
                record["example_phenotypes"].add(str(phenotype_name).strip())

    allele_consequences = {}
    for token, record in consequence_inventory.items():
        allele_consequences[token] = {
            "token": token,
            "gene_stem": record["gene_stem"],
            "allele_spec": record["allele_spec"],
            "categories": sorted(record["categories"]),
            "stage_terms": sorted(record["stage_terms"]),
            "context_terms": sorted(record["context_terms"]),
            "dominance_terms": sorted(record["dominance_terms"]),
            "reference_count": len(record["references"]),
            "example_phenotypes": sorted(record["example_phenotypes"])[:6],
            "conditional": bool(record["conditional"]),
            "source": "flybase_phenotype_consequence",
        }

    return {
        "allele_consequences": allele_consequences,
        "summary": {"cached_consequence_alleles": len(allele_consequences)},
    }


def _build_consequence_index_from_db(db):
    consequence_inventory = {}

    for row in db["flybase_phenotypes"].find(
        {},
        {
            "allele_tokens": 1,
            "phenotype_name": 1,
            "qualifier_names": 1,
            "qualifier_ids": 1,
            "reference": 1,
            "dominance_terms": 1,
        },
    ):
        phenotype_name = str(row.get("phenotype_name", "") or "")
        qualifier_names = "|".join(row.get("qualifier_names") or [])
        qualifier_ids = "|".join(row.get("qualifier_ids") or [])
        categories = _extract_consequence_categories(phenotype_name, qualifier_names, qualifier_ids)
        if not categories:
            continue

        stage_terms = _extract_stage_terms(phenotype_name, qualifier_names, qualifier_ids)
        context_terms = _extract_context_terms(phenotype_name, qualifier_names, qualifier_ids)
        dominance_terms = set(row.get("dominance_terms") or [])
        reference = str(row.get("reference", "") or "").strip()

        for token in row.get("allele_tokens") or []:
            match = ALLELE_TOKEN_RE.fullmatch(str(token or "").strip())
            if not match:
                continue
            gene_stem = match.group("gene")
            allele_spec = match.group("allele")
            record = consequence_inventory.setdefault(
                token,
                {
                    "token": token,
                    "gene_stem": gene_stem,
                    "allele_spec": allele_spec,
                    "categories": set(),
                    "stage_terms": set(),
                    "context_terms": set(),
                    "dominance_terms": set(),
                    "references": set(),
                    "example_phenotypes": set(),
                    "conditional": False,
                },
            )
            record["categories"].update(categories)
            record["stage_terms"].update(stage_terms)
            record["context_terms"].update(context_terms)
            record["dominance_terms"].update(dominance_terms)
            record["conditional"] = record["conditional"] or ("conditional" in context_terms)
            if reference:
                record["references"].add(reference)
            if phenotype_name:
                record["example_phenotypes"].add(phenotype_name)

    allele_consequences = {}
    for token, record in consequence_inventory.items():
        allele_consequences[token] = {
            "token": token,
            "gene_stem": record["gene_stem"],
            "allele_spec": record["allele_spec"],
            "categories": sorted(record["categories"]),
            "stage_terms": sorted(record["stage_terms"]),
            "context_terms": sorted(record["context_terms"]),
            "dominance_terms": sorted(record["dominance_terms"]),
            "reference_count": len(record["references"]),
            "example_phenotypes": sorted(record["example_phenotypes"])[:6],
            "conditional": bool(record["conditional"]),
            "source": "flybase_phenotype_consequence",
        }

    return {
        "allele_consequences": allele_consequences,
        "summary": {"cached_consequence_alleles": len(allele_consequences)},
    }
    if phenotype_path is None or stocks_path is None:
        return {
            "allele_markers": {},
            "summary": {"visible_rows": 0, "cached_alleles": 0},
            "stock_indexes": empty_stock_indexes,
        }

    stock_indexes = _stock_occurrence_indexes(stocks_path)
    allele_inventory = {}
    visible_row_count = 0

    for row, header_index in _iter_tsv_rows(phenotype_path):
        phenotype_name = row[header_index.get("phenotype_name", 2)]
        qualifier_names = row[header_index.get("qualifier_names", 4)] if len(row) > 4 else ""
        qualifier_ids = row[header_index.get("qualifier_ids", 5)] if len(row) > 5 else ""
        genotype_symbols = row[header_index.get("genotype_symbols", 0)]
        reference = row[header_index.get("reference", 6)] if len(row) > 6 else ""

        if not _is_unconditional_visible_row(phenotype_name, qualifier_names, qualifier_ids):
            continue

        visible_row_count += 1
        dominance_terms = _extract_dominance_terms(phenotype_name, qualifier_names, qualifier_ids)
        body_part_ids = _extract_body_part_ids(phenotype_name, qualifier_names, qualifier_ids)

        for match in ALLELE_TOKEN_RE.finditer(genotype_symbols or ""):
            token = match.group(0)
            gene_stem = match.group("gene")
            allele_spec = match.group("allele")
            record = allele_inventory.setdefault(
                token,
                {
                    "token": token,
                    "gene_stem": gene_stem,
                    "allele_spec": allele_spec,
                    "visible_rows": 0,
                    "references": set(),
                    "dominance_terms": set(),
                    "body_part_ids": set(),
                    "stock_occurrences": stock_indexes["allele_counter"].get(token, 0),
                    "gene_stock_occurrences": stock_indexes["gene_counter"].get(gene_stem, 0),
                },
            )
            record["visible_rows"] += 1
            if reference:
                record["references"].add(reference)
            record["dominance_terms"].update(dominance_terms)
            record["body_part_ids"].update(body_part_ids)

    allele_markers = {}
    for token, record in allele_inventory.items():
        if not _should_promote_allele_marker(record):
            continue
        allele_markers[token] = _derive_marker_record(
            token,
            record["gene_stem"],
            record["allele_spec"],
            {
                "visible_rows": record["visible_rows"],
                "reference_count": len(record["references"]),
                "dominance_terms": sorted(record["dominance_terms"]),
                "body_part_ids": sorted(record["body_part_ids"]),
                "stock_occurrences": record["stock_occurrences"],
            },
        )

    return {
        "allele_markers": allele_markers,
        "summary": {
            "visible_rows": visible_row_count,
            "cached_alleles": len(allele_markers),
            "stock_alleles": len(stock_indexes["allele_counter"]),
        },
        "stock_indexes": stock_indexes,
    }


def _build_allele_marker_index_from_db(db):
    stock_indexes = _stock_occurrence_indexes_from_db(db)
    allele_inventory = {}
    visible_row_count = 0

    for row in db["flybase_phenotypes"].find(
        {},
        {
            "allele_tokens": 1,
            "dominance_terms": 1,
            "body_part_ids": 1,
            "reference": 1,
            "is_unconditional_visible": 1,
        },
    ):
        if not row.get("is_unconditional_visible"):
            continue

        visible_row_count += 1
        dominance_terms = set(row.get("dominance_terms") or [])
        body_part_ids = set(row.get("body_part_ids") or [])
        reference = str(row.get("reference", "") or "").strip()

        for token in row.get("allele_tokens") or []:
            match = ALLELE_TOKEN_RE.fullmatch(str(token or "").strip())
            if not match:
                continue

            gene_stem = match.group("gene")
            allele_spec = match.group("allele")
            record = allele_inventory.setdefault(
                token,
                {
                    "token": token,
                    "gene_stem": gene_stem,
                    "allele_spec": allele_spec,
                    "visible_rows": 0,
                    "references": set(),
                    "dominance_terms": set(),
                    "body_part_ids": set(),
                    "stock_occurrences": stock_indexes["allele_counter"].get(token, 0),
                    "gene_stock_occurrences": stock_indexes["gene_counter"].get(gene_stem, 0),
                },
            )
            record["visible_rows"] += 1
            if reference:
                record["references"].add(reference)
            record["dominance_terms"].update(dominance_terms)
            record["body_part_ids"].update(body_part_ids)

    allele_markers = {}
    for token, record in allele_inventory.items():
        if not _should_promote_allele_marker(record):
            continue

        allele_markers[token] = _derive_marker_record(
            token,
            record["gene_stem"],
            record["allele_spec"],
            {
                "visible_rows": record["visible_rows"],
                "reference_count": len(record["references"]),
                "dominance_terms": sorted(record["dominance_terms"]),
                "body_part_ids": sorted(record["body_part_ids"]),
                "stock_occurrences": record["stock_occurrences"],
            },
        )

    return {
        "allele_markers": allele_markers,
        "summary": {
            "visible_rows": visible_row_count,
            "cached_alleles": len(allele_markers),
            "stock_alleles": len(stock_indexes["allele_counter"]),
        },
        "stock_indexes": stock_indexes,
    }


def _build_marker_alias_index(allele_markers):
    alias_candidates = {}

    for token, marker in allele_markers.items():
        match = ALLELE_TOKEN_RE.fullmatch(str(token or "").strip())
        if not match:
            continue

        allele_spec = match.group("allele")
        if not _is_safe_alias_candidate(allele_spec):
            continue

        alias_candidates.setdefault(_normalize_alias_key(allele_spec), set()).add(token)

    alias_index = {}
    ambiguous_aliases = {}
    for alias_key, tokens in alias_candidates.items():
        if len(tokens) == 1:
            alias_index[alias_key] = {"canonical_token": next(iter(tokens))}
            continue
        ambiguous_aliases[alias_key] = sorted(tokens)

    return {
        "alias_index": alias_index,
        "ambiguous_aliases": ambiguous_aliases,
        "summary": {
            "cached_unique_aliases": len(alias_index),
            "ambiguous_aliases": len(ambiguous_aliases),
        },
    }


def _construct_annotation_from_row(row, header_index, stock_indexes):
    construct_symbol = str(row[header_index.get("Transgenic Construct (symbol)", 2)] or "").strip()
    encoded_symbol = str(row[header_index.get("Encoded product/tool (symbol)", 8)] or "").strip()
    encoded_id = str(row[header_index.get("Encoded product/tool (id)", 9)] or "").strip()
    regulatory_symbol = str(row[header_index.get("Regulatory region (symbol)", 6)] or "").strip()
    tagged_symbol = str(row[header_index.get("Tagged with (symbol)", 10)] or "").strip()
    product_class = str(row[header_index.get("Transgenic Product class (term)", 4)] or "").strip()
    description = str(row[header_index.get("Description (text)", 14)] or "").strip()
    stocks_number = int(str(row[header_index.get("Stocks (number)", 16)] or "0") or 0)

    normalized_construct = _normalize_construct_token(construct_symbol)
    label = construct_symbol or encoded_symbol or regulatory_symbol or "construct"
    category = "construct"

    annotation_terms = [encoded_symbol, tagged_symbol, product_class, description]
    joined_terms = " ".join(term for term in annotation_terms if term)

    if any(keyword in joined_terms for keyword in REPORTER_KEYWORDS):
        category = "reporter"
    elif any(keyword in joined_terms for keyword in DRIVER_KEYWORDS):
        category = "driver"
    elif "RNAi" in joined_terms or "RNAI" in joined_terms:
        category = "rnai"
    elif "dominant_negative" in product_class or "dominant negative" in description.lower():
        category = "dominant_negative"

    if category == "driver" and regulatory_symbol and encoded_symbol:
        label = f"{regulatory_symbol}->{encoded_symbol}"
    elif category == "reporter" and encoded_symbol:
        label = f"{encoded_symbol} reporter"
    elif category == "rnai" and encoded_symbol:
        label = f"{encoded_symbol} RNAi"

    annotation = {
        "category": category,
        "label": label,
        "construct_symbol": construct_symbol,
        "normalized_construct": normalized_construct["prefix"],
        "construct_body": normalized_construct["body"],
        "encoded_symbol": encoded_symbol,
        "encoded_id": encoded_id,
        "regulatory_symbol": regulatory_symbol,
        "tagged_symbol": tagged_symbol,
        "product_class": product_class,
        "description": description,
        "stocks_number": stocks_number,
        "flybase_stock_occurrences": stock_indexes["construct_counter"].get(normalized_construct["prefix"], 0),
        "scoring_confidence": 0.7 if stocks_number or stock_indexes["construct_counter"].get(normalized_construct["prefix"], 0) else 0.58,
        "source": "flybase_construct_annotation",
    }
    return normalized_construct, annotation


def _build_construct_annotation_index(source_files, stock_indexes):
    construct_path = source_files.get("construct_descriptions")
    if construct_path is None:
        return {"construct_annotations": {}, "summary": {"cached_constructs": 0}}

    annotations_by_key = {}
    for row, header_index in _iter_tsv_rows(construct_path):
        normalized_construct, annotation = _construct_annotation_from_row(row, header_index, stock_indexes)
        if not normalized_construct["prefix"]:
            continue

        for key in {normalized_construct["prefix"], normalized_construct["body"]}:
            if not key:
                continue
            existing = annotations_by_key.get(key)
            if existing is None or annotation["stocks_number"] > existing["stocks_number"]:
                annotations_by_key[key] = annotation

    return {
        "construct_annotations": annotations_by_key,
        "summary": {"cached_constructs": len(annotations_by_key)},
    }


def _build_construct_annotation_index_from_db(db, stock_indexes):
    annotations_by_key = {}

    for row in db["flybase_constructs"].find(
        {},
        {
            "construct_symbol": 1,
            "encoded_product_symbol": 1,
            "encoded_product_id": 1,
            "regulatory_region_symbol": 1,
            "tagged_symbol": 1,
            "product_class_term": 1,
            "description": 1,
            "stocks_number": 1,
        },
    ):
        construct_symbol = str(row.get("construct_symbol", "") or "").strip()
        encoded_symbol = str(row.get("encoded_product_symbol", "") or "").strip()
        encoded_id = str(row.get("encoded_product_id", "") or "").strip()
        regulatory_symbol = str(row.get("regulatory_region_symbol", "") or "").strip()
        tagged_symbol = str(row.get("tagged_symbol", "") or "").strip()
        product_class = str(row.get("product_class_term", "") or "").strip()
        description = str(row.get("description", "") or "").strip()
        stocks_number = int(row.get("stocks_number") or 0)

        normalized_construct = _normalize_construct_token(construct_symbol)
        label = construct_symbol or encoded_symbol or regulatory_symbol or "construct"
        category = "construct"
        joined_terms = " ".join(
            term for term in (encoded_symbol, tagged_symbol, product_class, description) if term
        )

        if any(keyword in joined_terms for keyword in REPORTER_KEYWORDS):
            category = "reporter"
        elif any(keyword in joined_terms for keyword in DRIVER_KEYWORDS):
            category = "driver"
        elif "RNAi" in joined_terms or "RNAI" in joined_terms:
            category = "rnai"
        elif "dominant_negative" in product_class or "dominant negative" in description.lower():
            category = "dominant_negative"

        if category == "driver" and regulatory_symbol and encoded_symbol:
            label = f"{regulatory_symbol}->{encoded_symbol}"
        elif category == "reporter" and encoded_symbol:
            label = f"{encoded_symbol} reporter"
        elif category == "rnai" and encoded_symbol:
            label = f"{encoded_symbol} RNAi"

        annotation = {
            "category": category,
            "label": label,
            "construct_symbol": construct_symbol,
            "normalized_construct": normalized_construct["prefix"],
            "construct_body": normalized_construct["body"],
            "encoded_symbol": encoded_symbol,
            "encoded_id": encoded_id,
            "regulatory_symbol": regulatory_symbol,
            "tagged_symbol": tagged_symbol,
            "product_class": product_class,
            "description": description,
            "stocks_number": stocks_number,
            "flybase_stock_occurrences": stock_indexes["construct_counter"].get(normalized_construct["prefix"], 0),
            "scoring_confidence": 0.7 if stocks_number or stock_indexes["construct_counter"].get(normalized_construct["prefix"], 0) else 0.58,
            "source": "flybase_construct_annotation",
        }

        for key in {normalized_construct["prefix"], normalized_construct["body"]}:
            if not key:
                continue
            existing = annotations_by_key.get(key)
            if existing is None or annotation["stocks_number"] > existing["stocks_number"]:
                annotations_by_key[key] = annotation

    return {
        "construct_annotations": annotations_by_key,
        "summary": {"cached_constructs": len(annotations_by_key)},
    }


def _parse_split_stock_entry(entry):
    value = str(entry or "").strip()
    if not value or ":" not in value:
        return None
    _, genotype = value.split(":", 1)
    genotype = genotype.strip()
    qc_passed, normalized_or_error = qc_genotype(genotype)
    if not qc_passed:
        return None
    return normalized_or_error


def _build_split_system_index(source_files):
    split_path = source_files.get("split_system_combinations")
    if split_path is None:
        return {"split_systems": {}, "summary": {"cached_split_genotypes": 0}}

    split_systems = {}
    for row, header_index in _iter_tsv_rows(split_path):
        symbol = str(row[header_index.get("Symbol", 1)] or "").strip()
        component_alleles = str(row[header_index.get("Component_Alleles", 2)] or "").strip()
        stocks_field = str(row[header_index.get("Stocks", 3)] or "").strip()
        references = str(row[header_index.get("References", 5)] or "").strip()

        for stock_entry in _split_pipe_field(stocks_field):
            normalized_genotype = _parse_split_stock_entry(stock_entry)
            if not normalized_genotype:
                continue
            split_systems.setdefault(normalized_genotype, []).append(
                {
                    "category": "split_system",
                    "label": symbol,
                    "symbol": symbol,
                    "component_alleles": component_alleles,
                    "reference": references,
                    "stock_entry": stock_entry,
                    "source": "flybase_split_system",
                    "scoring_confidence": 0.82,
                }
            )

    return {
        "split_systems": split_systems,
        "summary": {"cached_split_genotypes": len(split_systems)},
    }


def _build_split_system_index_from_db(db):
    split_systems = {}

    for row in db["flybase_split_combinations"].find(
        {},
        {"symbol": 1, "component_alleles": 1, "stocks": 1, "references": 1},
    ):
        symbol = str(row.get("symbol", "") or "").strip()
        component_alleles = "|".join(row.get("component_alleles") or [])
        references = "|".join(row.get("references") or [])

        for stock_entry in row.get("stocks") or []:
            normalized_genotype = _parse_split_stock_entry(stock_entry)
            if not normalized_genotype:
                continue
            split_systems.setdefault(normalized_genotype, []).append(
                {
                    "category": "split_system",
                    "label": symbol,
                    "symbol": symbol,
                    "component_alleles": component_alleles,
                    "reference": references,
                    "stock_entry": stock_entry,
                    "source": "flybase_split_system",
                    "scoring_confidence": 0.82,
                }
            )

    return {
        "split_systems": split_systems,
        "summary": {"cached_split_genotypes": len(split_systems)},
    }


def build_flybase_phenotype_cache(data_dir=None, cache_path=None, force=False, db=None):
    data_dir = resolve_flybase_data_dir(data_dir)
    cache_path = resolve_flybase_phenotype_cache_path(data_dir, cache_path)
    signature = compute_flybase_pipeline_signature(data_dir)

    if not force and cache_path.exists():
        try:
            cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                isinstance(cached_payload, dict)
                and cached_payload.get("cache_version") == PHENOTYPE_EVIDENCE_CACHE_VERSION
                and cached_payload.get("source_signature") == signature
                and (db is None or cached_payload.get("source_kind") == "mongo_ingest")
            ):
                _IN_MEMORY_CACHE[str(cache_path)] = cached_payload
                return {
                    "cache_path": str(cache_path),
                    "rebuilt": False,
                    "source_signature": signature,
                    "source_kind": cached_payload.get("source_kind") or "files",
                    "summary": cached_payload.get("summary", {}),
                }
        except (OSError, json.JSONDecodeError):
            pass

    source_files = _collect_source_files(data_dir)
    if db is not None:
        allele_report = _build_allele_marker_index_from_db(db)
        construct_report = _build_construct_annotation_index_from_db(db, allele_report["stock_indexes"])
        split_report = _build_split_system_index_from_db(db)
        consequence_report = _build_consequence_index_from_db(db)
    else:
        allele_report = _build_allele_marker_index(source_files)
        construct_report = _build_construct_annotation_index(source_files, allele_report["stock_indexes"])
        split_report = _build_split_system_index(source_files)
        consequence_report = _build_consequence_index(source_files)
    alias_report = _build_marker_alias_index(allele_report["allele_markers"])

    payload = {
        "cache_version": PHENOTYPE_EVIDENCE_CACHE_VERSION,
        "generated_at": _timestamp(),
        "source_signature": signature,
        "source_kind": "mongo_ingest" if db is not None else "files",
        "source_files": {key: str(path) if path else "" for key, path in source_files.items()},
        "allele_markers": allele_report["allele_markers"],
        "marker_alias_index": alias_report["alias_index"],
        "ambiguous_marker_aliases": alias_report["ambiguous_aliases"],
        "construct_annotations": construct_report["construct_annotations"],
        "split_systems": split_report["split_systems"],
        "allele_consequences": consequence_report["allele_consequences"],
        "summary": {
            **allele_report["summary"],
            **alias_report["summary"],
            **construct_report["summary"],
            **split_report["summary"],
            **consequence_report["summary"],
        },
    }

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _IN_MEMORY_CACHE[str(cache_path)] = payload
    return {
        "cache_path": str(cache_path),
        "rebuilt": True,
        "source_signature": signature,
        "source_kind": payload["source_kind"],
        "summary": payload["summary"],
    }


def _empty_flybase_phenotype_cache_payload():
    return {
        "cache_version": PHENOTYPE_EVIDENCE_CACHE_VERSION,
        "generated_at": "",
        "source_signature": "",
        "source_kind": "files",
        "source_files": {},
        "allele_markers": {},
        "marker_alias_index": {},
        "ambiguous_marker_aliases": {},
        "construct_annotations": {},
        "split_systems": {},
        "allele_consequences": {},
        "summary": {},
        "available": False,
    }


def get_flybase_phenotype_cache(data_dir=None, cache_path=None, db=None):
    """Read-only accessor for the FlyBase phenotype evidence index.

    This never parses the raw FlyBase source files. The index is only ever
    (re)built by an explicit admin refresh action (build_flybase_phenotype_cache
    with force=True). If it hasn't been built yet, or the on-disk cache is
    stale relative to the current source files, lookups just degrade to empty
    results instead of blocking a request on a multi-minute parse.
    """
    data_dir = resolve_flybase_data_dir(data_dir)
    cache_path = resolve_flybase_phenotype_cache_path(data_dir, cache_path)
    cache_key = str(cache_path)

    cached_payload = _IN_MEMORY_CACHE.get(cache_key)
    if cached_payload:
        return cached_payload

    if cache_path.exists():
        try:
            cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached_payload = None
        if (
            isinstance(cached_payload, dict)
            and cached_payload.get("cache_version") == PHENOTYPE_EVIDENCE_CACHE_VERSION
        ):
            cached_payload.setdefault("available", True)
            _IN_MEMORY_CACHE[cache_key] = cached_payload
            return cached_payload

    return _empty_flybase_phenotype_cache_payload()


def flybase_phenotype_cache_status(data_dir=None, cache_path=None):
    """Cheap staleness check for the FlyBase evidence index (no parsing).

    Returns whether an index has ever been built, and whether the source
    files have changed since it was last built.
    """
    data_dir = resolve_flybase_data_dir(data_dir)
    cache_path = resolve_flybase_phenotype_cache_path(data_dir, cache_path)
    current_signature = compute_flybase_pipeline_signature(data_dir)

    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path)
    built = bool(cache.get("available"))
    stale = (not built) or (cache.get("source_signature") != current_signature)
    return {
        "built": built,
        "stale": stale,
        "generated_at": cache.get("generated_at", ""),
    }


def clear_flybase_phenotype_cache(cache_path=None):
    if cache_path is None:
        _IN_MEMORY_CACHE.clear()
        return
    _IN_MEMORY_CACHE.pop(str(Path(cache_path)), None)


def lookup_flybase_allele_marker(token, gene_stem=None, allele_spec=None, data_dir=None, cache_path=None, db=None):
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path, db=db)
    marker = cache.get("allele_markers", {}).get(str(token or "").strip())
    if marker is None:
        return None

    resolved_marker = deepcopy(marker)
    if gene_stem:
        resolved_marker.setdefault("gene_stem", gene_stem)
    if allele_spec:
        resolved_marker.setdefault("allele_spec", allele_spec)
    return resolved_marker


def lookup_flybase_marker_alias(alias_token, data_dir=None, cache_path=None, db=None):
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path, db=db)
    alias_key = _normalize_alias_key(alias_token)
    alias_record = cache.get("marker_alias_index", {}).get(alias_key)
    if alias_record is None:
        return None
    return deepcopy(alias_record)


def lookup_construct_annotations(construct_token, data_dir=None, cache_path=None, db=None):
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path, db=db)
    normalized_construct = _normalize_construct_token(construct_token)
    matches = []
    for key in (normalized_construct["prefix"], normalized_construct["body"]):
        if not key:
            continue
        annotation = cache.get("construct_annotations", {}).get(key)
        if annotation is None:
            continue
        hydrated = deepcopy(annotation)
        hydrated["construct_token"] = construct_token
        matches.append(hydrated)

    deduplicated = []
    seen = set()
    for annotation in matches:
        key = (annotation.get("category"), annotation.get("label"), annotation.get("construct_symbol"))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(annotation)
    return deduplicated


def lookup_split_system_annotations(normalized_genotype, data_dir=None, cache_path=None, db=None):
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path, db=db)
    annotations = cache.get("split_systems", {}).get(str(normalized_genotype or "").strip(), [])
    return [deepcopy(annotation) for annotation in annotations]


def lookup_flybase_allele_consequences(token, data_dir=None, cache_path=None, db=None):
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path, db=db)
    consequence = cache.get("allele_consequences", {}).get(str(token or "").strip())
    if consequence is None:
        return None
    return deepcopy(consequence)
