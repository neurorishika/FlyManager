import csv
import gzip
import logging
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from flymanager.utils.genetics import (get_bloomington_data,
                                       get_stock_genotype, qc_genotype)

REPO_ROOT = Path(__file__).resolve().parents[2]
FLYBASE_STOCKS_PATH = REPO_ROOT / "data" / "flybase" / "stocks_FB2026_01.tsv.gz"
FLYBASE_STOCKS_TEXT_PATH = REPO_ROOT / "data" / "flybase" / "stocks_FB2026_01.tsv"
FLYBASE_INSERTION_MAPPING_PATH = REPO_ROOT / "data" / "flybase" / "insertion_mapping_fb_2026_01.tsv.gz"
FLYBASE_INSERTION_MAPPING_TEXT_PATH = REPO_ROOT / "data" / "flybase" / "insertion_mapping_fb_2026_01.tsv"
FLYBASE_FBAL_TO_FBGN_PATH = REPO_ROOT / "data" / "flybase" / "fbal_to_fbgn_fb_2026_01.tsv.gz"
FLYBASE_FBAL_TO_FBGN_TEXT_PATH = REPO_ROOT / "data" / "flybase" / "fbal_to_fbgn_fb_2026_01.tsv"
FLYBASE_GENE_MAP_TABLE_PATH = REPO_ROOT / "data" / "flybase" / "gene_map_table_fb_2026_01.tsv.gz"
FLYBASE_GENE_MAP_TABLE_TEXT_PATH = REPO_ROOT / "data" / "flybase" / "gene_map_table_fb_2026_01.tsv"
LOGGER = logging.getLogger(__name__)
GROUP_PAIRS = {"{": "}", "[": "]", "(": ")"}


SOURCE_TYPE_TO_COLLECTION = {
    "BDSC": "Bloomington",
    "VIENNA": "Vienna",
    "NIG": "NIG-Fly",
    "KYOTO": "Kyoto",
    "KDRC": "KDRC",
    "FLYORF": "FlyORF",
    "NDSSC": "NDSSC",
}

SOURCE_LABEL_TO_TYPE = {
    "bdsc": "BDSC",
    "bloomington": "BDSC",
    "vienna": "VIENNA",
    "vdrc": "VIENNA",
    "nig": "NIG",
    "nig-fly": "NIG",
    "kyoto": "KYOTO",
    "kdrc": "KDRC",
    "flyorf": "FLYORF",
    "ndssc": "NDSSC",
}

EXTERNAL_SOURCE_OPTIONS = [
    ("BDSC", "BDSC (Bloomington)"),
    ("VIENNA", "Vienna"),
    ("NIG", "NIG-Fly"),
    ("KYOTO", "Kyoto"),
    ("KDRC", "KDRC"),
    ("FLYORF", "FlyORF"),
    ("NDSSC", "NDSSC"),
]

PROVIDER_PORTAL_URLS = {
    "BDSC": "https://bdsc.indiana.edu/stocks/",
    "VIENNA": "https://shop.vbc.ac.at/vdrc_store/",
    "NIG": "https://shigen.nig.ac.jp/fly/nigfly/",
    "KYOTO": "https://kyotofly.kit.jp/",
    "KDRC": "https://www.flyrnai.org/KDRC/",
    "FLYORF": "https://www.flyorf.ch/",
    "NDSSC": "https://stockcenter.nd.edu/",
}

PROVIDER_LINK_BUILDERS = {
    "BDSC": lambda source_id: f"https://bdsc.indiana.edu/stocks/{quote_plus(source_id)}",
    "VIENNA": lambda source_id: f"https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q={quote_plus(source_id)}",
}

FLYBASE_STOCK_REPORT_URL = "https://flybase.org/reports/{flybase_stock_id}.html"


def _get_stock_index_cache_key(stocks_path=None):
    resolved_path = resolve_flybase_stocks_path(stocks_path)
    stat_result = resolved_path.stat()
    return str(resolved_path), stat_result.st_mtime_ns, stat_result.st_size


@lru_cache(maxsize=4)
def _load_flybase_stock_indexes(cache_key):
    resolved_path = Path(cache_key[0])
    rows_by_collection_stock = {}
    rows_by_fbst = {}
    rows_by_stock_number = {}
    dmel_rows = []

    for row in _iter_flybase_stock_rows(resolved_path):
        if row.get("species") != "Dmel":
            continue

        dmel_rows.append(row)

        collection_short_name = str(row.get("collection_short_name", "") or "").strip()
        stock_number = str(row.get("stock_number", "") or "").strip()
        fbst = str(row.get("FBst", "") or "").strip()

        if collection_short_name and stock_number:
            rows_by_collection_stock[(collection_short_name, stock_number)] = row
            rows_by_stock_number.setdefault(stock_number, []).append(row)

        if fbst:
            rows_by_fbst[fbst] = row

    return {
        "rows_by_collection_stock": rows_by_collection_stock,
        "rows_by_fbst": rows_by_fbst,
        "rows_by_stock_number": rows_by_stock_number,
        "dmel_rows": tuple(dmel_rows),
    }


def _get_flybase_stock_indexes(stocks_path=None):
    return _load_flybase_stock_indexes(_get_stock_index_cache_key(stocks_path))


def preload_flybase_stock_indexes(stocks_path=None):
    return _get_flybase_stock_indexes(stocks_path)


def get_external_stock_record(source_type, source_id):
    normalized_source_type = (source_type or "").strip().upper()
    normalized_source_id = str(source_id or "").strip()

    if not normalized_source_id:
        return None, "Source ID is required."

    if normalized_source_type == "FLYBASE":
        return _build_flybase_stock_payload_by_fbst(normalized_source_id)

    if normalized_source_type == "BDSC":
        return _build_bdsc_stock_payload(normalized_source_id)

    if normalized_source_type in SOURCE_TYPE_TO_COLLECTION:
        return _build_flybase_stock_payload_by_collection(
            SOURCE_TYPE_TO_COLLECTION[normalized_source_type],
            normalized_source_type,
            normalized_source_id,
        )

    return None, f"Source type '{source_type}' is not supported for autopopulate."


def build_external_stock_provider_link(source_type, source_id, flybase_stock_id=""):
    normalized_source_type = (source_type or "").strip().upper()
    normalized_source_id = str(source_id or "").strip()
    normalized_flybase_stock_id = str(flybase_stock_id or "").strip()

    if normalized_source_type in PROVIDER_LINK_BUILDERS and normalized_source_id:
        return {
            "url": PROVIDER_LINK_BUILDERS[normalized_source_type](normalized_source_id),
            "label": f"Open {SOURCE_TYPE_TO_COLLECTION.get(normalized_source_type, normalized_source_type)} provider page",
            "kind": "provider",
        }

    portal_url = PROVIDER_PORTAL_URLS.get(normalized_source_type)
    if portal_url:
        return {
            "url": portal_url,
            "label": f"Open {SOURCE_TYPE_TO_COLLECTION.get(normalized_source_type, normalized_source_type)} portal",
            "kind": "portal",
        }

    if normalized_flybase_stock_id:
        return {
            "url": FLYBASE_STOCK_REPORT_URL.format(
                flybase_stock_id=quote_plus(normalized_flybase_stock_id)
            ),
            "label": "Open FlyBase stock report",
            "kind": "reference",
        }

    return None


def resolve_stock_source_type(stock_record):
    direct_source_type = str(
        stock_record.get("StockSource") or stock_record.get("stockSource") or ""
    ).strip().upper()
    if direct_source_type in SOURCE_TYPE_TO_COLLECTION or direct_source_type == "FLYBASE":
        return direct_source_type

    candidate_values = [
        stock_record.get("SourceCollection"),
        stock_record.get("sourceCollection"),
        stock_record.get("Provenance"),
        stock_record.get("provenance"),
    ]
    for raw_value in candidate_values:
        source_type = _source_type_from_value(raw_value)
        if source_type:
            return source_type

    return ""


def resolve_stock_source_collection(stock_record):
    direct_source_collection = str(
        stock_record.get("SourceCollection") or stock_record.get("sourceCollection") or ""
    ).strip()
    if direct_source_collection:
        return direct_source_collection

    source_type = resolve_stock_source_type(stock_record)
    if source_type:
        return SOURCE_TYPE_TO_COLLECTION.get(source_type, "")

    return ""


def enrich_stock_source_context(stock_record, stocks_path=None):
    source_id = str(
        stock_record.get("SourceID") or stock_record.get("sourceID") or ""
    ).strip()
    source_type = resolve_stock_source_type(stock_record)
    source_collection = resolve_stock_source_collection(stock_record)
    flybase_stock_id = _resolve_target_flybase_stock_id(stock_record)

    matched_row = None
    if source_type and source_id:
        matched_row = _find_flybase_stock_row(
            collection_short_name=source_collection
            or SOURCE_TYPE_TO_COLLECTION.get(source_type, ""),
            stock_number=source_id,
            stocks_path=stocks_path,
        )

    if matched_row is None and source_id:
        matched_row = _infer_source_row_from_record(stock_record, stocks_path=stocks_path)

    if matched_row is not None:
        source_collection = source_collection or str(
            matched_row.get("collection_short_name", "") or ""
        ).strip()
        if not source_type:
            inferred_source_type = _source_type_for_collection(source_collection)
            source_type = "" if inferred_source_type == "FLYBASE" else inferred_source_type
        if not flybase_stock_id:
            flybase_stock_id = str(matched_row.get("FBst", "") or "").strip()

    provider_link = build_external_stock_provider_link(
        source_type,
        source_id,
        flybase_stock_id,
    )

    return {
        "sourceType": source_type,
        "sourceCollection": source_collection,
        "flyBaseStockID": flybase_stock_id,
        "providerURL": provider_link["url"] if provider_link else "",
        "providerLinkLabel": provider_link["label"] if provider_link else "",
        "providerLinkKind": provider_link["kind"] if provider_link else "",
    }


def build_stock_provider_metadata(stock_record):
    source_context = enrich_stock_source_context(stock_record)
    return build_stock_provider_metadata_from_context(source_context)


def build_stock_provider_metadata_from_context(source_context):
    return {
        "providerURL": source_context["providerURL"],
        "providerLinkLabel": source_context["providerLinkLabel"],
        "providerLinkKind": source_context["providerLinkKind"],
    }


def find_external_stock_matches(target_stock_record, stocks_path=None, max_results=20):
    target_source_type = resolve_stock_source_type(target_stock_record)
    target_source_id = str(
        target_stock_record.get("SourceID") or target_stock_record.get("sourceID") or ""
    ).strip()
    target_flybase_stock_id = _resolve_target_flybase_stock_id(target_stock_record)
    target_normalized_genotype = _resolve_target_normalized_genotype(target_stock_record)
    target_raw_genotype = str(
        target_stock_record.get("ExternalRawGenotype")
        or target_stock_record.get("rawGenotype")
        or target_stock_record.get("Genotype")
        or target_stock_record.get("genotype")
        or ""
    ).strip()

    if not (target_flybase_stock_id or target_normalized_genotype or target_raw_genotype):
        return []

    matches = []
    seen_candidates = set()

    flybase_indexes = _get_flybase_stock_indexes(stocks_path)
    for row in flybase_indexes["dmel_rows"]:

        source_type = _source_type_for_collection(row.get("collection_short_name", ""))
        if source_type == "FLYBASE":
            continue

        source_id = str(row.get("stock_number", "") or "").strip()
        if not source_id:
            continue

        if source_type == target_source_type and source_id == target_source_id:
            continue

        score, reasons = _score_external_stock_match(
            row,
            target_flybase_stock_id=target_flybase_stock_id,
            target_normalized_genotype=target_normalized_genotype,
            target_raw_genotype=target_raw_genotype,
        )
        if not reasons:
            continue

        dedupe_key = (source_type, source_id)
        if dedupe_key in seen_candidates:
            continue

        candidate_payload = _build_flybase_payload_from_row(row, source_type, source_id)
        candidate_payload["matchScore"] = score
        candidate_payload["matchReasons"] = reasons

        matches.append(candidate_payload)
        seen_candidates.add(dedupe_key)

    matches.sort(
        key=lambda candidate: (
            -candidate.get("matchScore", 0),
            candidate.get("sourceCollection", ""),
            candidate.get("sourceID", ""),
        )
    )
    return matches[:max_results]


def _build_bdsc_stock_payload(stock_id):
    flybase_payload = None
    flybase_row = _find_flybase_stock_row(
        collection_short_name="Bloomington",
        stock_number=stock_id,
    )
    if flybase_row is not None:
        flybase_payload = _build_flybase_payload_from_row(flybase_row, "BDSC", stock_id)
        if flybase_payload["supportStatus"] == "supported":
            return flybase_payload, None
        LOGGER.info(
            "BDSC stock %s falling back to legacy Bloomington normalization because FlyBase genotype is not yet compatible: %s",
            stock_id,
            flybase_payload["supportReason"],
        )
    else:
        LOGGER.info(
            "BDSC stock %s falling back to legacy Bloomington normalization because no FlyBase Bloomington row was found",
            stock_id,
        )

    legacy_payload, legacy_error = _build_legacy_bloomington_stock_payload(
        stock_id,
        flybase_row=flybase_row,
    )
    if legacy_payload is not None:
        LOGGER.info(
            "BDSC stock %s resolved via legacy Bloomington fallback with supportStatus=%s",
            stock_id,
            legacy_payload["supportStatus"],
        )
        return legacy_payload, None

    if flybase_row is not None:
        LOGGER.warning(
            "BDSC stock %s could not be normalized via legacy Bloomington fallback; returning FlyBase payload with supportStatus=%s",
            stock_id,
            flybase_payload["supportStatus"] if flybase_payload is not None else "unknown",
        )
        return flybase_payload, None

    LOGGER.warning(
        "BDSC stock %s could not be found in FlyBase or legacy Bloomington fallback: %s",
        stock_id,
        legacy_error,
    )
    return None, legacy_error


def _build_legacy_bloomington_stock_payload(stock_id, flybase_row=None):
    dataframe = get_bloomington_data()
    stock_rows = dataframe[dataframe["Stk #"].astype(str) == str(stock_id)]
    if stock_rows.empty:
        return None, "Stock ID not found"

    stock_row = stock_rows.iloc[0]
    raw_genotype = str(stock_row.get("Genotype", "") or "").strip()
    normalized_genotype, normalization_error = get_stock_genotype(stock_id)
    support_status = "supported" if normalization_error is None else "unsupported"
    support_reason = "" if normalization_error is None else normalization_error

    return _with_provider_metadata({
        "stockSource": "BDSC",
        "sourceCollection": "Bloomington",
        "sourceID": str(stock_id),
        "flyBaseStockID": flybase_row.get("FBst", "") if flybase_row else "",
        "name": f"Bloomington {stock_id}",
        "altReference": flybase_row.get("FBst", "") if flybase_row else "",
        "species": DEFAULT_SPECIES,
        "provenance": "Bloomington",
        "genotype": normalized_genotype if normalization_error is None else "",
        "rawGenotype": raw_genotype,
        "supportStatus": support_status,
        "supportReason": support_reason,
    }), None


def _build_flybase_stock_payload_by_collection(collection_short_name, source_type, stock_id):
    row = _find_flybase_stock_row(
        collection_short_name=collection_short_name,
        stock_number=stock_id,
    )
    if row is None:
        return None, f"No {collection_short_name} stock with ID {stock_id} was found in FlyBase."

    return _build_flybase_payload_from_row(row, source_type, stock_id), None


def _build_flybase_stock_payload_by_fbst(flybase_stock_id):
    row = _find_flybase_stock_row(fbst=flybase_stock_id)
    if row is None:
        return None, f"No FlyBase stock with ID {flybase_stock_id} was found."

    source_type = _source_type_for_collection(row.get("collection_short_name", ""))
    return _build_flybase_payload_from_row(
        row,
        source_type,
        row.get("stock_number", ""),
    ), None


def _build_flybase_payload_from_row(row, source_type, source_id):
    raw_genotype = str(row.get("FB_genotype", "") or "").strip()
    description = str(row.get("description", "") or "").strip()
    qc_passed, normalized_or_error = normalize_external_stock_genotype(raw_genotype)
    support_reason = "" if qc_passed else normalized_or_error

    return _with_provider_metadata({
        "stockSource": source_type,
        "sourceCollection": row.get("collection_short_name", ""),
        "sourceID": str(source_id or "").strip(),
        "flyBaseStockID": row.get("FBst", ""),
        "name": description or raw_genotype or f"{row.get('collection_short_name', 'FlyBase')} {source_id}",
        "altReference": row.get("FBst", ""),
        "species": SPECIES_LABELS.get(row.get("species", ""), row.get("species", "")),
        "provenance": row.get("collection_short_name", "FlyBase"),
        "genotype": normalized_or_error if qc_passed else "",
        "rawGenotype": raw_genotype,
        "supportStatus": "supported" if qc_passed else "unsupported",
        "supportReason": support_reason,
    })


def _with_provider_metadata(payload):
    payload.update(build_stock_provider_metadata(payload))
    return payload


def _resolve_target_flybase_stock_id(target_stock_record):
    direct_flybase_stock_id = str(
        target_stock_record.get("FlyBaseStockID")
        or target_stock_record.get("flyBaseStockID")
        or ""
    ).strip()
    if direct_flybase_stock_id:
        return direct_flybase_stock_id

    alt_reference = str(
        target_stock_record.get("AltReference") or target_stock_record.get("altReference") or ""
    ).strip()
    if alt_reference.startswith("FBst"):
        return alt_reference

    source_type = str(
        target_stock_record.get("StockSource") or target_stock_record.get("stockSource") or ""
    ).strip().upper()
    source_id = str(
        target_stock_record.get("SourceID") or target_stock_record.get("sourceID") or ""
    ).strip()
    if source_type == "FLYBASE" and source_id.startswith("FBst"):
        return source_id

    return ""


def _resolve_target_normalized_genotype(target_stock_record):
    genotype = str(
        target_stock_record.get("Genotype") or target_stock_record.get("genotype") or ""
    ).strip()
    if genotype:
        qc_passed, normalized_or_error = qc_genotype(genotype)
        if qc_passed:
            return normalized_or_error

    raw_genotype = str(
        target_stock_record.get("ExternalRawGenotype")
        or target_stock_record.get("rawGenotype")
        or ""
    ).strip()
    if not raw_genotype:
        return ""

    qc_passed, normalized_or_error = normalize_external_stock_genotype(raw_genotype)
    return normalized_or_error if qc_passed else ""


def _score_external_stock_match(
    row,
    *,
    target_flybase_stock_id,
    target_normalized_genotype,
    target_raw_genotype,
):
    score = 0
    reasons = []

    row_flybase_stock_id = str(row.get("FBst", "") or "").strip()
    if target_flybase_stock_id and row_flybase_stock_id == target_flybase_stock_id:
        score = max(score, 100)
        reasons.append("Same FlyBase stock ID")

    row_raw_genotype = str(row.get("FB_genotype", "") or "").strip()
    if target_normalized_genotype:
        qc_passed, normalized_or_error = normalize_external_stock_genotype(row_raw_genotype)
        if qc_passed and normalized_or_error == target_normalized_genotype:
            score = max(score, 95)
            reasons.append("Exact normalized genotype match")

    if target_raw_genotype and row_raw_genotype and row_raw_genotype == target_raw_genotype:
        score = max(score, 85)
        reasons.append("Exact raw genotype match")

    deduped_reasons = []
    for reason in reasons:
        if reason not in deduped_reasons:
            deduped_reasons.append(reason)

    return score, deduped_reasons


def _resolve_flybase_bulk_path(explicit_path, primary_path, text_path, label):
    if explicit_path is not None:
        resolved_path = Path(explicit_path)
        if not resolved_path.exists():
            raise FileNotFoundError(f"{label} file not found at {resolved_path}")
        return resolved_path

    for candidate in (primary_path, text_path):
        if candidate.exists():
            return candidate

    raise FileNotFoundError(f"{label} file not found at {primary_path} or {text_path}")


def resolve_flybase_insertion_mapping_path(insertion_mapping_path=None):
    return _resolve_flybase_bulk_path(
        insertion_mapping_path,
        FLYBASE_INSERTION_MAPPING_PATH,
        FLYBASE_INSERTION_MAPPING_TEXT_PATH,
        "FlyBase insertion mapping",
    )


def resolve_flybase_fbal_to_fbgn_path(fbal_to_fbgn_path=None):
    return _resolve_flybase_bulk_path(
        fbal_to_fbgn_path,
        FLYBASE_FBAL_TO_FBGN_PATH,
        FLYBASE_FBAL_TO_FBGN_TEXT_PATH,
        "FlyBase allele-to-gene",
    )


def resolve_flybase_gene_map_table_path(gene_map_table_path=None):
    return _resolve_flybase_bulk_path(
        gene_map_table_path,
        FLYBASE_GENE_MAP_TABLE_PATH,
        FLYBASE_GENE_MAP_TABLE_TEXT_PATH,
        "FlyBase gene map table",
    )


def _iter_flybase_annotation_rows(path):
    resolved_path = Path(path)
    opener = gzip.open if resolved_path.suffix == ".gz" else open

    with opener(resolved_path, "rt", encoding="utf-8") as handle:
        header = None
        for line in handle:
            stripped_line = line.rstrip("\n")
            if stripped_line.startswith("#"):
                if "\t" not in stripped_line:
                    continue
                header_fields = stripped_line.lstrip("#").split("\t")
                if header_fields and header_fields[0] == "":
                    header_fields = header_fields[1:]
                header = header_fields
                continue

            if header is None or not stripped_line:
                continue

            yield dict(zip(header, stripped_line.split("\t")))


def _chromosome_index_from_location(location_text):
    arm = str(location_text or "").split(":", 1)[0].strip().upper()
    if arm == "X":
        return 0
    if arm in ("2L", "2R", "2"):
        return 1
    if arm in ("3L", "3R", "3"):
        return 2
    if arm == "4":
        return 3
    return None


def _chromosome_index_from_recombination_loc(recombination_loc_text):
    # Old classical linkage-map genes (no modern sequence coordinates) often
    # only carry a recombination position like "2-52.5", using the same
    # 1=X,2,3,4 chromosome numbering as bloomington.csv's "Ch # all" column.
    prefix = str(recombination_loc_text or "").split("-", 1)[0].strip().upper()
    if prefix in ("1", "X"):
        return 0
    if prefix == "2":
        return 1
    if prefix == "3":
        return 2
    if prefix == "4":
        return 3
    return None


def _find_all_top_level_groups(text):
    groups = []
    closing_stack = []
    group_start_stack = []

    for index, character in enumerate(text):
        if character in GROUP_PAIRS:
            if not closing_stack:
                group_start_stack.append((character, index))
            closing_stack.append(GROUP_PAIRS[character])
            continue

        if character in GROUP_PAIRS.values():
            if closing_stack and character == closing_stack[-1]:
                closing_stack.pop()
                if not closing_stack:
                    open_character, start_index = group_start_stack.pop()
                    groups.append((open_character, start_index, index))

    return groups


def _find_top_level_bracket_groups(text):
    return [group for group in _find_all_top_level_groups(text) if group[0] in ("{", "[")]


def _extract_site_name_candidate(text):
    # FlyBase's own nomenclature grammar for site-specific transgenes is
    # "Vector{payload}site-name" (e.g. "PBac{...}VK00005") or, when the site
    # itself disrupts a host gene, "Gene[site-name]" (e.g. "Msp300[attP40]").
    # Reading the last top-level bracket group under that grammar recovers
    # the site name for any current or future site-naming scheme without
    # needing to guess what such names look like via a regex character class.
    normalized_text = str(text or "")
    groups = _find_top_level_bracket_groups(normalized_text)
    if not groups:
        return None

    open_character, start_index, end_index = groups[-1]
    if open_character == "[":
        candidate = normalized_text[start_index + 1:end_index]
    else:
        candidate = normalized_text[end_index + 1:]

    candidate = candidate.strip()
    return candidate or None


def _extract_brace_payload(text):
    normalized_text = str(text or "")
    for open_character, start_index, end_index in _find_top_level_bracket_groups(normalized_text):
        if open_character == "{":
            return normalized_text[start_index + 1:end_index]
    return None


# insertion_mapping.tsv carries a genomic_location for ~70k ordinary,
# one-off insertions (any sequence-mapped transposon), not just reusable
# docking sites - so it can't be used wholesale as a site-name source: e.g.
# "Doc{}Ubx[1]" structurally extracts a "1" candidate that collides with an
# ordinary allele suffix like "cn[1]". Docking sites are instead created by
# a small, closed set of FlyBase-standard vector payloads that have been
# stable for ~15 years (Groth 2004, Venken 2006, Bischof 2007); restricting
# to rows using one of those payloads reliably isolates genuine reusable
# docking-site definitions while the site *name* itself is still read
# structurally, so any current or future site name under these systems
# (VK00041, a new ZH-* line, etc.) resolves with no code change.
DOCKING_SITE_VECTOR_PAYLOAD_PREFIXES = ("y[+]-attP-",)
DOCKING_SITE_VECTOR_PAYLOAD_SUFFIXES = (".attP",)
DOCKING_SITE_VECTOR_PAYLOAD_EXACT = {"CaryP"}


def _is_docking_site_vector_payload(payload):
    if not payload:
        return False
    if payload in DOCKING_SITE_VECTOR_PAYLOAD_EXACT:
        return True
    if payload.startswith(DOCKING_SITE_VECTOR_PAYLOAD_PREFIXES):
        return True
    return payload.endswith(DOCKING_SITE_VECTOR_PAYLOAD_SUFFIXES)


def _get_landing_site_cache_key(insertion_mapping_path=None):
    resolved_path = resolve_flybase_insertion_mapping_path(insertion_mapping_path)
    stat_result = resolved_path.stat()
    return str(resolved_path), stat_result.st_mtime_ns, stat_result.st_size


@lru_cache(maxsize=4)
def _load_landing_site_chromosome_map(cache_key):
    resolved_path = Path(cache_key[0])
    site_chromosome_index = {}

    for row in _iter_flybase_annotation_rows(resolved_path):
        genomic_location = str(row.get("genomic_location", "") or "").strip()
        if not genomic_location:
            continue

        insertion_symbol = row.get("insertion_symbol", "")
        if not _is_docking_site_vector_payload(_extract_brace_payload(insertion_symbol)):
            continue

        site_name = _extract_site_name_candidate(insertion_symbol)
        if not site_name:
            continue

        chromosome_index = _chromosome_index_from_location(genomic_location)
        if chromosome_index is None:
            continue

        site_chromosome_index.setdefault(site_name, chromosome_index)

    return site_chromosome_index


def _get_landing_site_chromosome_map(insertion_mapping_path=None):
    return _load_landing_site_chromosome_map(_get_landing_site_cache_key(insertion_mapping_path))


def _get_gene_map_cache_key(gene_map_table_path=None):
    resolved_path = resolve_flybase_gene_map_table_path(gene_map_table_path)
    stat_result = resolved_path.stat()
    return str(resolved_path), stat_result.st_mtime_ns, stat_result.st_size


@lru_cache(maxsize=4)
def _load_gene_symbol_chromosome_map(cache_key):
    resolved_path = Path(cache_key[0])
    gene_chromosome_index = {}

    for row in _iter_flybase_annotation_rows(resolved_path):
        symbol = str(row.get("current_symbol", "") or "").strip()
        if not symbol:
            continue

        chromosome_index = _chromosome_index_from_location(row.get("sequence_loc", ""))
        if chromosome_index is None:
            chromosome_index = _chromosome_index_from_recombination_loc(
                row.get("recombination_loc", "")
            )
        if chromosome_index is None:
            continue

        gene_chromosome_index.setdefault(symbol, chromosome_index)

    return gene_chromosome_index


def _get_gene_symbol_chromosome_map(gene_map_table_path=None):
    return _load_gene_symbol_chromosome_map(_get_gene_map_cache_key(gene_map_table_path))


def _get_allele_cache_key(fbal_to_fbgn_path=None, gene_map_table_path=None):
    resolved_fbal_path = resolve_flybase_fbal_to_fbgn_path(fbal_to_fbgn_path)
    stat_result = resolved_fbal_path.stat()
    return (
        (str(resolved_fbal_path), stat_result.st_mtime_ns, stat_result.st_size),
        _get_gene_map_cache_key(gene_map_table_path),
    )


@lru_cache(maxsize=4)
def _load_allele_symbol_chromosome_map(cache_key):
    fbal_cache_key, gene_map_cache_key = cache_key
    gene_chromosome_index = _load_gene_symbol_chromosome_map(gene_map_cache_key)
    resolved_path = Path(fbal_cache_key[0])
    allele_chromosome_index = {}

    for row in _iter_flybase_annotation_rows(resolved_path):
        allele_symbol = str(row.get("AlleleSymbol", "") or "").strip()
        gene_symbol = str(row.get("GeneSymbol", "") or "").strip()
        if not allele_symbol or not gene_symbol:
            continue

        chromosome_index = gene_chromosome_index.get(gene_symbol)
        if chromosome_index is None:
            continue

        allele_chromosome_index.setdefault(allele_symbol, chromosome_index)

    return allele_chromosome_index


def _get_allele_symbol_chromosome_map(fbal_to_fbgn_path=None, gene_map_table_path=None):
    return _load_allele_symbol_chromosome_map(
        _get_allele_cache_key(fbal_to_fbgn_path, gene_map_table_path)
    )


def preload_flybase_chromosome_lookup_tables():
    # Building these from the raw TSVs takes a few seconds (mostly gzip
    # decompression + line parsing over ~70k-220k rows) - call this once at
    # app startup so that cost lands off the request path instead of
    # stalling whichever request first triggers a genotype resolution.
    _get_landing_site_chromosome_map()
    _get_gene_symbol_chromosome_map()
    _get_allele_symbol_chromosome_map()


def _bare_gene_symbol(token):
    # Strip only a trailing allele-designator "[...]" to recover the gene
    # symbol, and skip past any leading vector "{...}" payload first - a
    # naive "stop at the first [ or (" regex breaks on gene names that
    # legitimately contain parens (su(pr)[3], tu(2)bw[36a]) and on
    # transposon-prefixed disruption tokens whose first "[" is nested
    # inside the vector's own payload (P{w[+mC]=lacW}pnt[1277] -> "pnt",
    # not "P{w").
    #
    # Also reports whether a real "[...]" allele bracket was found and
    # stripped. Without one, the "symbol" is just whatever trailed a
    # vector's "{...}" payload verbatim (e.g. the bare "D" in
    # "P{...}D") - BDSC's convention for distinguishing independent
    # insertion lines of the same construct, not a gene reference, and
    # it collides too easily with real short gene symbols (D=Dichaete,
    # H=Hairless, ...) to be trusted as one.
    normalized_token = str(token or "").strip()
    groups = _find_all_top_level_groups(normalized_token)

    search_start = 0
    for open_character, _start_index, end_index in groups:
        if open_character == "{":
            search_start = end_index + 1

    bracket_start_index = len(normalized_token)
    for open_character, start_index, _end_index in groups:
        if open_character == "[" and start_index >= search_start:
            bracket_start_index = start_index
            break

    had_allele_bracket = bracket_start_index < len(normalized_token)
    symbol = normalized_token[search_start:bracket_start_index].strip()
    return symbol, had_allele_bracket


def _resolve_chromosome_index_for_segment(
    segment_text,
    *,
    insertion_mapping_path=None,
    fbal_to_fbgn_path=None,
    gene_map_table_path=None,
):
    normalized_segment = str(segment_text or "").strip()
    if not normalized_segment:
        return None

    if ";" in normalized_segment:
        # A semicolon surviving top-level splitting must be nested inside a
        # bracket/paren group - the "a;b" of a compound rearrangement like
        # "T(2;3)ap[Xa]" that genuinely spans two chromosomes in one
        # segment. That can't be placed in a single chromosome slot, so
        # decline rather than silently attribute it to whichever chromosome
        # a token elsewhere in the segment happens to resolve to.
        return None

    site_chromosome_index = _get_landing_site_chromosome_map(insertion_mapping_path)
    allele_chromosome_index = _get_allele_symbol_chromosome_map(
        fbal_to_fbgn_path, gene_map_table_path
    )
    gene_chromosome_index = _get_gene_symbol_chromosome_map(gene_map_table_path)

    for homolog in _split_top_level(normalized_segment, {"/"}, keep_empty=True):
        for token in _split_top_level(homolog, {" "}, keep_empty=False):
            cleaned_token = token.strip()
            if not cleaned_token:
                continue

            site_name_candidate = _extract_site_name_candidate(cleaned_token)
            if site_name_candidate and site_name_candidate in site_chromosome_index:
                return site_chromosome_index[site_name_candidate]

            if cleaned_token in allele_chromosome_index:
                return allele_chromosome_index[cleaned_token]

            bare_symbol, had_allele_bracket = _bare_gene_symbol(cleaned_token)
            if had_allele_bracket and bare_symbol in gene_chromosome_index:
                return gene_chromosome_index[bare_symbol]

    return None


def _place_segments_by_resolved_chromosome(chromosome_parts):
    non_empty_segments = [
        (index, segment)
        for index, segment in enumerate(chromosome_parts)
        if segment.strip()
    ]

    if not non_empty_segments:
        return chromosome_parts + [""] * (4 - len(chromosome_parts)), None

    resolved_indices = []
    for _, segment in non_empty_segments:
        chromosome_index = _resolve_chromosome_index_for_segment(segment)
        if chromosome_index is None:
            return None, (
                f"Could not determine which chromosome '{segment}' belongs to "
                "from FlyBase gene/allele or docking-site data"
            )
        resolved_indices.append(chromosome_index)

    if len(set(resolved_indices)) != len(resolved_indices):
        return None, (
            "Multiple genotype segments resolved to the same chromosome; "
            "cannot place unambiguously"
        )

    placed_parts = ["", "", "", ""]
    for (_, segment), chromosome_index in zip(non_empty_segments, resolved_indices):
        placed_parts[chromosome_index] = segment

    return placed_parts, None


def _split_top_level(text, separators, *, keep_empty=False):
    tokens = []
    current = []
    closing_stack = []

    for character in str(text or ""):
        if character in GROUP_PAIRS:
            closing_stack.append(GROUP_PAIRS[character])
            current.append(character)
            continue

        if character in GROUP_PAIRS.values():
            if closing_stack and character == closing_stack[-1]:
                closing_stack.pop()
            current.append(character)
            continue

        if not closing_stack and character in separators:
            token = "".join(current).strip()
            if token or keep_empty:
                tokens.append(token)
            current = []
            continue

        current.append(character)

    token = "".join(current).strip()
    if token or keep_empty:
        tokens.append(token)
    return tokens


def _normalize_external_chromosome_field(chromosome_field):
    normalized_field = str(chromosome_field or "").strip()
    homologs = _split_top_level(normalized_field, {"/"}, keep_empty=True)
    if len(homologs) > 2:
        return False, (
            "Chromosomes must be in the format chromosomeA/chromosomeB "
            "(heterozygous) or chromosomeBoth (homozygous)"
        )

    if len(homologs) == 2:
        homologs = [homolog.strip() for homolog in homologs]
        homologs.sort()
        return True, "/".join(homologs)

    return True, normalized_field


def normalize_external_stock_genotype(raw_genotype):
    cleaned_genotype = str(raw_genotype or "").strip()
    chromosome_parts = _split_top_level(cleaned_genotype, {";"}, keep_empty=True)
    top_level_semicolon_count = max(len(chromosome_parts) - 1, 0)

    if top_level_semicolon_count == 0 and cleaned_genotype.count(";") == 0:
        return False, "Genotype must be in the format xchromosome; chromosome2; chromosome3; chromosome4"

    if top_level_semicolon_count not in {1, 2, 3}:
        return False, "Genotype must be in the format xchromosome; chromosome2; chromosome3; chromosome4"

    if len(chromosome_parts) > 4:
        return False, "Genotype must be in the format xchromosome; chromosome2; chromosome3; chromosome4"

    if len(chromosome_parts) < 4:
        resolved_parts, resolution_error = _place_segments_by_resolved_chromosome(chromosome_parts)
        if resolution_error is not None:
            return False, resolution_error
        chromosome_parts = resolved_parts

    normalized_parts = []
    for chromosome_field in chromosome_parts[:4]:
        qc_passed, normalized_or_error = _normalize_external_chromosome_field(
            chromosome_field
        )
        if not qc_passed:
            return False, normalized_or_error
        normalized_parts.append(normalized_or_error)

    return True, "; ".join(normalized_parts)


def collect_compatible_gene_metadata_from_flybase(stocks_path=None):
    resolved_stocks_path = resolve_flybase_stocks_path(stocks_path)
    gene_components = {0: set(), 1: set(), 2: set(), 3: set()}
    summary = {
        "total_rows": 0,
        "dmel_rows": 0,
        "supported_rows": 0,
        "unsupported_rows": 0,
        "source_path": str(resolved_stocks_path),
    }

    for row in _iter_flybase_stock_rows(resolved_stocks_path):
        summary["total_rows"] += 1

        if row.get("species") != "Dmel":
            continue

        summary["dmel_rows"] += 1
        supported, normalized_or_error = normalize_external_stock_genotype(
            row.get("FB_genotype", "")
        )

        if not supported:
            summary["unsupported_rows"] += 1
            continue

        summary["supported_rows"] += 1
        chromosome_fields = [field.strip() for field in normalized_or_error.split(";")]

        for chromosome_index, chromosome_field in enumerate(chromosome_fields[:4]):
            if not chromosome_field:
                continue

            for component in chromosome_field.split("/"):
                normalized_component = component.strip()
                if not normalized_component or normalized_component == "0":
                    continue
                gene_components[chromosome_index].add(normalized_component)

    return {
        chromosome_index: sorted(components)
        for chromosome_index, components in gene_components.items()
    }, summary


def _source_type_for_collection(collection_short_name):
    for source_type, collection_name in SOURCE_TYPE_TO_COLLECTION.items():
        if collection_name == collection_short_name:
            return source_type
    return "FLYBASE"


def _source_type_from_value(value):
    text = str(value or "").strip()
    if not text:
        return ""

    fragments = [
        _normalize_source_fragment(fragment)
        for fragment in re.split(r"[/,;|]", text)
        if fragment.strip()
    ]
    if not fragments:
        fragments = [_normalize_source_fragment(text)]

    for fragment in fragments:
        mapped = SOURCE_LABEL_TO_TYPE.get(fragment)
        if mapped:
            return mapped

    normalized_text = _normalize_source_fragment(text)
    for label, mapped in SOURCE_LABEL_TO_TYPE.items():
        if label in normalized_text:
            return mapped

    return ""


def _normalize_source_fragment(value):
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9.+-]+", " ", text).strip()


def _find_flybase_stock_row(collection_short_name=None, stock_number=None, fbst=None, stocks_path=None):
    normalized_collection = str(collection_short_name or "").strip()
    normalized_stock_number = str(stock_number or "").strip()
    normalized_fbst = str(fbst or "").strip()
    flybase_indexes = _get_flybase_stock_indexes(stocks_path)

    if normalized_fbst:
        return flybase_indexes["rows_by_fbst"].get(normalized_fbst)

    if normalized_collection and normalized_stock_number:
        return flybase_indexes["rows_by_collection_stock"].get(
            (normalized_collection, normalized_stock_number)
        )

    return None


def _infer_source_row_from_record(stock_record, stocks_path=None):
    source_id = str(
        stock_record.get("SourceID") or stock_record.get("sourceID") or ""
    ).strip()
    if not source_id:
        return None

    flybase_indexes = _get_flybase_stock_indexes(stocks_path)
    candidates = [
        row
        for row in flybase_indexes["rows_by_stock_number"].get(source_id, [])
        if _source_type_for_collection(row.get("collection_short_name", "")) != "FLYBASE"
    ]

    if not candidates:
        return None

    preferred_source_type = _source_type_from_value(
        stock_record.get("StockSource")
        or stock_record.get("stockSource")
        or stock_record.get("SourceCollection")
        or stock_record.get("sourceCollection")
        or stock_record.get("Provenance")
        or stock_record.get("provenance")
    )
    if preferred_source_type:
        preferred_candidates = [
            row
            for row in candidates
            if _source_type_for_collection(row.get("collection_short_name", ""))
            == preferred_source_type
        ]
        if len(preferred_candidates) == 1:
            return preferred_candidates[0]
        if preferred_candidates:
            candidates = preferred_candidates

    target_flybase_stock_id = _resolve_target_flybase_stock_id(stock_record)
    target_normalized_genotype = _resolve_target_normalized_genotype(stock_record)
    target_raw_genotype = str(
        stock_record.get("ExternalRawGenotype")
        or stock_record.get("rawGenotype")
        or stock_record.get("Genotype")
        or stock_record.get("genotype")
        or ""
    ).strip()

    scored_candidates = []
    for row in candidates:
        score, _ = _score_external_stock_match(
            row,
            target_flybase_stock_id=target_flybase_stock_id,
            target_normalized_genotype=target_normalized_genotype,
            target_raw_genotype=target_raw_genotype,
        )
        scored_candidates.append((score, row))

    scored_candidates.sort(
        key=lambda item: (
            -item[0],
            item[1].get("collection_short_name", ""),
            item[1].get("FBst", ""),
        )
    )

    if scored_candidates and scored_candidates[0][0] > 0:
        if len(scored_candidates) == 1 or scored_candidates[1][0] < scored_candidates[0][0]:
            return scored_candidates[0][1]

    if len(candidates) == 1:
        return candidates[0]

    return None


def _iter_flybase_stock_rows(stocks_path):
    resolved_stocks_path = resolve_flybase_stocks_path(stocks_path)
    opener = gzip.open if resolved_stocks_path.suffix == ".gz" else open

    with opener(resolved_stocks_path, "rt", encoding="utf-8") as handle:
        header = None
        for line in handle:
            if line.startswith("##"):
                continue
            if line.startswith("#"):
                header = line.lstrip("#").rstrip("\n").split("\t")
                break
            header = line.rstrip("\n").split("\t")
            break

        if header is None:
            return

        reader = csv.DictReader(handle, fieldnames=header, delimiter="\t")
        for row in reader:
            yield row


DEFAULT_SPECIES = "D. melanogaster"
SPECIES_LABELS = {
    "Dmel": DEFAULT_SPECIES,
}


def resolve_flybase_stocks_path(stocks_path=None):
    if stocks_path is not None:
        resolved_path = Path(stocks_path)
        if not resolved_path.exists():
            raise FileNotFoundError(f"FlyBase stocks file not found at {resolved_path}")
        return resolved_path

    for candidate in (FLYBASE_STOCKS_PATH, FLYBASE_STOCKS_TEXT_PATH):
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"FlyBase stocks file not found at {FLYBASE_STOCKS_PATH} or {FLYBASE_STOCKS_TEXT_PATH}"
    )