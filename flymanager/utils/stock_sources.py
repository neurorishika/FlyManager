import csv
import gzip
import logging
from pathlib import Path

from flymanager.utils.genetics import (get_bloomington_data,
                                       get_stock_genotype, qc_genotype)

REPO_ROOT = Path(__file__).resolve().parents[2]
FLYBASE_STOCKS_PATH = REPO_ROOT / "data" / "flybase" / "stocks_FB2026_01.tsv.gz"
FLYBASE_STOCKS_TEXT_PATH = REPO_ROOT / "data" / "flybase" / "stocks_FB2026_01.tsv"
LOGGER = logging.getLogger(__name__)

SOURCE_TYPE_TO_COLLECTION = {
    "BDSC": "Bloomington",
    "VIENNA": "Vienna",
    "NIG": "NIG-Fly",
    "KYOTO": "Kyoto",
    "KDRC": "KDRC",
    "FLYORF": "FlyORF",
    "NDSSC": "NDSSC",
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

    return {
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
    }, None


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

    return {
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
    }


def normalize_external_stock_genotype(raw_genotype):
    cleaned_genotype = str(raw_genotype or "").strip()
    qc_passed, normalized_or_error = qc_genotype(cleaned_genotype)
    if qc_passed:
        return True, normalized_or_error

    # Many FlyBase stock records omit trailing empty chromosome fields.
    semicolon_count = cleaned_genotype.count(";")
    if 0 < semicolon_count < 3:
        chromosome_parts = [part.strip() for part in cleaned_genotype.split(";")]
        padded_candidate = "; ".join(chromosome_parts + [""] * (4 - len(chromosome_parts)))
        padded_qc_passed, padded_normalized_or_error = qc_genotype(padded_candidate)
        if padded_qc_passed:
            return True, padded_normalized_or_error
        return False, padded_normalized_or_error

    return False, normalized_or_error


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


def _find_flybase_stock_row(collection_short_name=None, stock_number=None, fbst=None, stocks_path=None):
    normalized_collection = str(collection_short_name or "").strip()
    normalized_stock_number = str(stock_number or "").strip()
    normalized_fbst = str(fbst or "").strip()
    resolved_stocks_path = resolve_flybase_stocks_path(stocks_path)

    for row in _iter_flybase_stock_rows(resolved_stocks_path):
        if row.get("species") != "Dmel":
            continue
        if normalized_fbst and row.get("FBst") == normalized_fbst:
            return row
        if normalized_collection and normalized_stock_number:
            if (
                str(row.get("collection_short_name", "")).strip() == normalized_collection
                and str(row.get("stock_number", "")).strip() == normalized_stock_number
            ):
                return row
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