import csv
import gzip
import re
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from fuzzywuzzy import fuzz

from flymanager.utils.phenotypes.flybase_pipeline import (
    compute_flybase_pipeline_signature, get_flybase_phenotype_cache,
    resolve_flybase_data_dir, resolve_flybase_phenotype_cache_path)
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.visual_markers import (
    get_allele_marker_tokens, get_gene_marker_symbols,
    get_reviewed_marker_alias)

DEFAULT_CANDIDATE_LIMIT = 6
MIN_FUZZY_SCORE = 55
ALLELE_DESCRIPTION_PREFIX = "dmel_classical_and_insertion_allele_descriptions"


def _find_matching_file(data_dir, prefix):
    return next(
        iter(
            sorted(
                path
                for pattern in (f"{prefix}*.tsv", f"{prefix}*.tsv.gz")
                for path in Path(data_dir).glob(pattern)
            )
        ),
        None,
    )


def _file_stamp(path):
    path = Path(path)
    if not path.exists():
        return ""
    stat_result = path.stat()
    return f"{path}:{stat_result.st_mtime_ns}:{stat_result.st_size}"


def _open_text_file(file_path):
    file_path = Path(file_path)
    if file_path.suffix == ".gz":
        return gzip.open(file_path, "rt", encoding="utf-8", newline="")
    return file_path.open("r", encoding="utf-8", newline="")


def _iter_tsv_rows(file_path):
    with _open_text_file(file_path) as handle:
        reader = csv.reader(handle, delimiter="\t")
        headers = None
        header_index = {}
        for row in reader:
            if not row:
                continue
            if len(row) == 1 and row[0].startswith("#"):
                continue
            if row[0].startswith("#") and len(row) > 1:
                headers = list(row)
                headers[0] = headers[0].lstrip("#")
                header_index = {name: index for index, name in enumerate(headers)}
                continue
            if headers is None:
                headers = row
                header_index = {name: index for index, name in enumerate(headers)}
                continue
            yield row, header_index


def _normalize_search_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _display_source_label(source_key):
    return str(source_key or "").replace("_", " ").strip() or "FlyBase"


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


def _upsert_candidate(index, canonical_token, *, candidate_kind, source, search_terms=None, **fields):
    canonical = str(canonical_token or "").strip()
    if not canonical:
        return

    candidate = index.setdefault(
        canonical,
        {
            "canonical_token": canonical,
            "candidate_kind": candidate_kind,
            "source_labels": set(),
            "search_terms": set(),
            "display_label": canonical,
            "description": "",
            "flybase_id": "",
            "gene_symbol": "",
            "insertion_symbol": "",
            "regulatory_region_symbol": "",
            "encoded_product_symbol": "",
            "stocks_number": 0,
        },
    )

    candidate["source_labels"].add(source)
    candidate["search_terms"].add(canonical)
    for term in search_terms or []:
        normalized = str(term or "").strip()
        if normalized:
            candidate["search_terms"].add(normalized)

    if candidate["candidate_kind"] != "construct" and candidate_kind == "construct":
        candidate["candidate_kind"] = "construct"

    for key, value in fields.items():
        if value in (None, ""):
            continue
        if key == "stocks_number":
            candidate[key] = max(int(value or 0), int(candidate.get(key) or 0))
            continue
        existing = str(candidate.get(key) or "")
        incoming = str(value)
        if not existing or len(incoming) > len(existing):
            candidate[key] = incoming


@lru_cache(maxsize=4)
def _load_candidate_index(data_dir_text, cache_path_text, allele_file_stamp, cache_file_stamp):
    del allele_file_stamp, cache_file_stamp

    data_dir = Path(data_dir_text)
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path_text)
    index = {}

    for annotation in cache.get("construct_annotations", {}).values():
        canonical = str(annotation.get("construct_symbol") or annotation.get("normalized_construct") or "").strip()
        _upsert_candidate(
            index,
            canonical,
            candidate_kind="construct",
            source="FlyBase construct annotation",
            display_label=annotation.get("label") or canonical,
            description=annotation.get("description") or "",
            flybase_id=annotation.get("encoded_id") or "",
            regulatory_region_symbol=annotation.get("regulatory_symbol") or "",
            encoded_product_symbol=annotation.get("encoded_symbol") or "",
            stocks_number=annotation.get("stocks_number") or 0,
            search_terms=[
                annotation.get("construct_body"),
                annotation.get("label"),
                annotation.get("regulatory_symbol"),
                annotation.get("encoded_symbol"),
                annotation.get("tagged_symbol"),
                annotation.get("product_class"),
                annotation.get("description"),
            ],
        )

    allele_descriptions_path = _find_matching_file(data_dir, ALLELE_DESCRIPTION_PREFIX)
    if allele_descriptions_path is not None:
        for row, header_index in _iter_tsv_rows(allele_descriptions_path):
            allele_symbol = str(row[header_index.get("Allele (symbol)", 0)] or "").strip()
            _upsert_candidate(
                index,
                allele_symbol,
                candidate_kind="construct" if allele_symbol.startswith(("P{", "PBac{", "Mi{", "TI{", "M{")) else "allele",
                source="FlyBase allele description",
                display_label=allele_symbol,
                description=str(row[header_index.get("Description (text)", 18)] or "").strip(),
                flybase_id=str(row[header_index.get("Allele (id)", 1)] or "").strip(),
                gene_symbol=str(row[header_index.get("Gene (symbol)", 2)] or "").strip(),
                insertion_symbol=str(row[header_index.get("Insertion (symbol)", 6)] or "").strip(),
                regulatory_region_symbol=str(row[header_index.get("Regulatory region (symbol)", 10)] or "").strip(),
                encoded_product_symbol=str(row[header_index.get("Encoded product/tool (symbol)", 12)] or "").strip(),
                stocks_number=str(row[header_index.get("Stocks (number)", 20)] or "0").strip() or 0,
                search_terms=[
                    row[header_index.get("Insertion (symbol)", 6)] if len(row) > 6 else "",
                    row[header_index.get("Gene (symbol)", 2)] if len(row) > 2 else "",
                    row[header_index.get("Regulatory region (symbol)", 10)] if len(row) > 10 else "",
                    row[header_index.get("Encoded product/tool (symbol)", 12)] if len(row) > 12 else "",
                    row[header_index.get("Description (text)", 18)] if len(row) > 18 else "",
                ],
            )

    candidates = []
    for candidate in index.values():
        search_terms = tuple(sorted(candidate.pop("search_terms")))
        source_labels = sorted(candidate.pop("source_labels"))
        candidates.append(
            {
                **candidate,
                "source": " + ".join(source_labels),
                "_search_terms": search_terms,
            }
        )
    return tuple(candidates)


def _candidate_index(data_dir=None, cache_path=None):
    resolved_data_dir = resolve_flybase_data_dir(data_dir)
    resolved_cache_path = resolve_flybase_phenotype_cache_path(resolved_data_dir, cache_path)
    allele_descriptions_path = _find_matching_file(resolved_data_dir, ALLELE_DESCRIPTION_PREFIX)
    return _load_candidate_index(
        str(resolved_data_dir),
        str(resolved_cache_path),
        _file_stamp(allele_descriptions_path) if allele_descriptions_path else "",
        _file_stamp(resolved_cache_path),
    )


def _score_candidate(query, candidate):
    query_text = str(query or "").strip()
    query_lower = query_text.lower()
    query_key = _normalize_search_key(query_text)
    best_score = 0
    best_basis = candidate.get("canonical_token", "")

    for term in candidate.get("_search_terms", (candidate.get("canonical_token", ""),)):
        term_text = str(term or "").strip()
        if not term_text:
            continue
        term_lower = term_text.lower()
        term_key = _normalize_search_key(term_text)

        if len(term_key) < 3 and term_key != query_key:
            continue

        score = max(
            fuzz.ratio(query_lower, term_lower),
            fuzz.partial_ratio(query_lower, term_lower),
            fuzz.token_sort_ratio(query_lower, term_lower),
        )

        if len(term_key) < 5:
            score = min(score, fuzz.ratio(query_lower, term_lower))
        if query_key and term_key == query_key:
            score = 100
        elif query_key and term_key and query_key in term_key:
            score = max(score, 90)

        if query_key and term_key and term_key in query_key and len(term_key) < max(5, len(query_key) // 2):
            score = min(score, 60)

        if score > best_score:
            best_score = score
            best_basis = term_text

    return best_score, best_basis


def search_flybase_standardization_candidates(query, *, limit=DEFAULT_CANDIDATE_LIMIT, data_dir=None, cache_path=None):
    query_text = str(query or "").strip()
    if not query_text:
        return []

    # A limit of 0 means the caller wants issue detection only, not ranked
    # candidates (e.g. the bulk reviewer / materialization path). Scoring every
    # entry in the FlyBase candidate index just to slice ``matches[:0]`` is pure
    # wasted work, so short-circuit before the linear fuzzy scan.
    if limit is not None and limit <= 0:
        return []

    matches = []
    for candidate in _candidate_index(data_dir=data_dir, cache_path=cache_path):
        score, basis = _score_candidate(query_text, candidate)
        if score < MIN_FUZZY_SCORE:
            continue
        matches.append(
            {
                "canonical_token": candidate["canonical_token"],
                "candidate_kind": candidate["candidate_kind"],
                "display_label": candidate.get("display_label") or candidate["canonical_token"],
                "description": candidate.get("description") or "",
                "flybase_id": candidate.get("flybase_id") or "",
                "gene_symbol": candidate.get("gene_symbol") or "",
                "insertion_symbol": candidate.get("insertion_symbol") or "",
                "regulatory_region_symbol": candidate.get("regulatory_region_symbol") or "",
                "encoded_product_symbol": candidate.get("encoded_product_symbol") or "",
                "stocks_number": int(candidate.get("stocks_number") or 0),
                "source": candidate.get("source") or "FlyBase",
                "match_score": score,
                "match_basis": basis,
            }
        )

    matches.sort(
        key=lambda item: (
            -item["match_score"],
            -int(item.get("stocks_number") or 0),
            -len(item.get("canonical_token") or ""),
            item.get("canonical_token") or "",
        )
    )
    return matches[:limit]


def _issue_label(issue_type):
    if issue_type == "unresolved_token":
        return "Bare token needs review"
    return "Standard-format allele lacks canonical mapping"


def _merge_recommended_candidate(candidates, recommended_replacement, recommended_source):
    canonical_token = str(recommended_replacement or "").strip()
    if not canonical_token:
        return candidates

    merged = []
    seen_canonical = set()
    recommended_present = False
    for candidate in candidates:
        current_canonical = str(candidate.get("canonical_token") or "").strip()
        if current_canonical in seen_canonical:
            continue
        seen_canonical.add(current_canonical)
        if current_canonical == canonical_token:
            recommended_present = True
        merged.append(candidate)

    if recommended_present:
        return merged

    return [
        {
            "canonical_token": canonical_token,
            "candidate_kind": "reviewed_alias",
            "display_label": canonical_token,
            "description": "Curated reviewed alias already mapped to this canonical token.",
            "flybase_id": "",
            "gene_symbol": "",
            "insertion_symbol": "",
            "regulatory_region_symbol": "",
            "encoded_product_symbol": "",
            "stocks_number": 0,
            "source": _display_source_label(recommended_source),
            "match_score": 100,
            "match_basis": canonical_token,
        }
    ] + merged


def review_stock_standardization(genotype, *, token_search_overrides=None, candidate_limit=DEFAULT_CANDIDATE_LIMIT, data_dir=None, cache_path=None):
    genotype_text = str(genotype or "").strip()
    token_search_overrides = token_search_overrides or {}
    cache = get_flybase_phenotype_cache(data_dir=data_dir, cache_path=cache_path)
    alias_index = cache.get("marker_alias_index", {})
    ambiguous_aliases = cache.get("ambiguous_marker_aliases", {})

    known_gene_stems = get_gene_marker_symbols()
    known_allele_tokens = get_allele_marker_tokens()
    issues = {}

    for package in _iter_genotype_packages(genotype_text):
        parsed = parse_gene_package(package)

        for allele in parsed["classical_alleles"]:
            token = allele["token"]
            if token in known_allele_tokens or allele["gene_stem"] in known_gene_stems:
                continue
            record = issues.setdefault(
                token,
                {"token": token, "issue_type": "standard_format_unmodeled", "occurrence_count": 0},
            )
            record["occurrence_count"] += 1

        for token in parsed["unresolved"]:
            if token in known_gene_stems:
                continue
            record = issues.setdefault(
                token,
                {"token": token, "issue_type": "unresolved_token", "occurrence_count": 0},
            )
            record["occurrence_count"] += 1

    issue_payloads = []
    for token, record in sorted(
        issues.items(),
        key=lambda item: (item[1]["issue_type"] != "unresolved_token", item[0].lower()),
    ):
        normalized = str(token or "").strip().lower()
        recommended_replacement = ""
        recommended_source = ""

        reviewed_alias = get_reviewed_marker_alias(token)
        if reviewed_alias is not None:
            recommended_replacement = str(reviewed_alias.get("value") or "").strip()
            recommended_source = "reviewed_alias"
        else:
            alias_record = alias_index.get(normalized)
            if alias_record is not None:
                recommended_replacement = str(alias_record.get("canonical_token") or "").strip()
                recommended_source = "flybase_alias"

        search_query = str(token_search_overrides.get(token) or token).strip()
        fuzzy_candidates = search_flybase_standardization_candidates(
            search_query,
            limit=candidate_limit,
            data_dir=data_dir,
            cache_path=cache_path,
        )
        fuzzy_candidates = _merge_recommended_candidate(
            fuzzy_candidates,
            recommended_replacement,
            recommended_source,
        )
        if candidate_limit is not None:
            fuzzy_candidates = fuzzy_candidates[:candidate_limit]
        issue_payloads.append(
            {
                "token": token,
                "issue_type": record["issue_type"],
                "issue_label": _issue_label(record["issue_type"]),
                "occurrence_count": int(record["occurrence_count"]),
                "recommended_replacement": recommended_replacement,
                "recommended_source": recommended_source,
                "ambiguous_candidates": list(ambiguous_aliases.get(normalized, [])),
                "search_query": search_query,
                "fuzzy_candidates": fuzzy_candidates,
            }
        )

    summary = Counter(issue["issue_type"] for issue in issue_payloads)
    return {
        "genotype": genotype_text,
        "issue_count": len(issue_payloads),
        "summary": dict(summary),
        "issues": issue_payloads,
    }


# ---------------------------------------------------------------------------
# Materialized standardization cache
#
# The bulk reviewer page must not run the full standardization review (a per
# token pass over the genotype) for every accessible stock/cross on every
# request. Instead we materialize a compact per-genotype summary onto each
# document, mirroring the ``PhenotypeCache`` pattern: a versioned payload
# stamped with the FlyBase pipeline signature so it can be detected as stale
# and recomputed on write or during a backfill. The bulk path only ever needs
# issue detection, so the summary is built with ``candidate_limit=0`` and never
# triggers the expensive fuzzy candidate scan.
# ---------------------------------------------------------------------------

STANDARDIZATION_CACHE_VERSION = 1


def _standardization_cache_timestamp():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def summarize_genotype_standardization(genotype):
    """Compact standardization summary for a single genotype string.

    Contains exactly what the reviewer list renders per row, and nothing that
    requires the fuzzy candidate scan (``candidate_limit=0``).
    """
    review = review_stock_standardization(str(genotype or ""), candidate_limit=0)
    issues = list(review.get("issues") or [])
    top_tokens = [issue.get("token", "") for issue in issues[:4] if issue.get("token")]
    recommended_replacements = [
        {
            "from": issue.get("token", ""),
            "to": issue.get("recommended_replacement", ""),
        }
        for issue in issues
        if issue.get("token") and issue.get("recommended_replacement")
    ]
    review_summary = review.get("summary") or {}
    issue_count = int(review.get("issue_count") or 0)

    return {
        "issueCount": issue_count,
        "hasIssues": bool(issue_count),
        "unresolvedCount": int(review_summary.get("unresolved_token", 0)),
        "unmodeledCount": int(review_summary.get("standard_format_unmodeled", 0)),
        "topTokens": top_tokens,
        "recommendedReplacements": recommended_replacements[:3],
    }


def build_stock_standardization_cache(genotype):
    genotype_text = str(genotype or "")
    return {
        "version": STANDARDIZATION_CACHE_VERSION,
        "computedAt": _standardization_cache_timestamp(),
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "genotype": genotype_text,
        "summary": summarize_genotype_standardization(genotype_text),
    }


def build_cross_standardization_cache(male_genotype, female_genotype):
    male_text = str(male_genotype or "")
    female_text = str(female_genotype or "")
    return {
        "version": STANDARDIZATION_CACHE_VERSION,
        "computedAt": _standardization_cache_timestamp(),
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "maleGenotype": male_text,
        "femaleGenotype": female_text,
        "male": summarize_genotype_standardization(male_text),
        "female": summarize_genotype_standardization(female_text),
    }


def _standardization_cache_is_current(cache, strict):
    """Shared version/signature staleness check.

    strict=True (backfill/refresh paths) also requires the cache version and
    FlyBase pipeline signature to match the current pipeline. strict=False
    (ordinary read paths) serves whatever is stored; only the genotype match is
    still enforced by the callers as a correctness guard.
    """
    if not isinstance(cache, dict):
        return False
    if not strict:
        return True
    if cache.get("version") != STANDARDIZATION_CACHE_VERSION:
        return False
    signature = str(cache.get("pipelineSignature", ""))
    if signature and signature != compute_flybase_pipeline_signature():
        return False
    return True


def get_cached_stock_standardization(record, strict=True):
    cache = record.get("StandardizationCache")
    genotype = str(record.get("Genotype", ""))

    if not _standardization_cache_is_current(cache, strict):
        return None
    if str(cache.get("genotype", "")) != genotype:
        return None
    if not isinstance(cache.get("summary"), dict):
        return None
    return cache


def get_cached_cross_standardization(record, strict=True):
    cache = record.get("StandardizationCache")
    male_genotype = str(record.get("MaleGenotype", ""))
    female_genotype = str(record.get("FemaleGenotype", ""))

    if not _standardization_cache_is_current(cache, strict):
        return None
    if str(cache.get("maleGenotype", "")) != male_genotype:
        return None
    if str(cache.get("femaleGenotype", "")) != female_genotype:
        return None
    if not isinstance(cache.get("male"), dict) or not isinstance(cache.get("female"), dict):
        return None
    return cache