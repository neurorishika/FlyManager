import re
from copy import deepcopy

from flymanager.utils.genetics import qc_genotype
from flymanager.utils.phenotypes.parser import parse_gene_package

CYTOLOGY_TOKEN_RE = re.compile(r"\b\d{1,3}[A-F](?:\d{1,2})?\b", re.IGNORECASE)
CYTOLOGY_POINT_RE = re.compile(r"^(?P<band>\d{1,3})(?P<letter>[A-F])(?P<subband>\d{0,2})$", re.IGNORECASE)

DEFAULT_BALANCER_PRIORITY = {
    1: ["FM7c", "FM7a", "FM7", "FM3"],
    2: ["CyO", "SM6a", "SM5", "SM1"],
    3: ["TM3", "TM6B", "TM6", "TM2", "TM1"],
    4: [],
}


def normalize_chromosome_label(value):
    text = str(value or "").strip().upper()
    if text == "X":
        return 1
    if text in {"1", "2", "3", "4"}:
        return int(text)
    return None


def normalize_target_sex(target_sex):
    normalized = str(target_sex or "female").strip().lower()
    if normalized not in {"male", "female"}:
        raise ValueError("Sex must be either 'male' or 'female'")
    return normalized


def normalize_genotype(genotype):
    qc_passed, normalized_or_error = qc_genotype(genotype)
    if not qc_passed:
        raise ValueError(normalized_or_error)
    return normalized_or_error


def chromosome_copies(chromosome_text, chromosome_index, target_sex):
    field = (chromosome_text or "").strip()
    if chromosome_index == 0:
        if target_sex == "male":
            return [field or "+"]
        if "/" in field:
            return [part.strip() or "+" for part in field.split("/")]
        return [field or "+", field or "+"]

    if "/" in field:
        return [part.strip() or "+" for part in field.split("/")]
    return [field or "+", field or "+"]


def iter_genotype_packages(genotype, target_sex):
    normalized = normalize_genotype(genotype)
    sex = normalize_target_sex(target_sex)
    for chromosome_index, chromosome_text in enumerate(normalized.split(";")):
        chromosome_number = chromosome_index + 1
        for copy_index, package in enumerate(chromosome_copies(chromosome_text.strip(), chromosome_index, sex)):
            yield {
                "chromosome_index": chromosome_index,
                "chromosome": chromosome_number,
                "copy_index": copy_index,
                "package": package,
                "parsed": parse_gene_package(package),
            }


def get_collection(db, name):
    if db is None:
        return None
    try:
        return db[name]
    except Exception:
        return None


def iter_collection_documents(collection, query=None):
    if collection is None:
        return []
    try:
        return list(collection.find(query or {}))
    except TypeError:
        return list(collection.find())


def deepcopy_documents(documents):
    return [deepcopy(document) for document in documents]


def lookup_gene_map_record(gene_symbol, db):
    collection = get_collection(db, "flybase_gene_map")
    if collection is None:
        return None

    symbol = str(gene_symbol or "").strip()
    if not symbol:
        return None

    try:
        exact = collection.find_one({"current_symbol": symbol})
    except Exception:
        exact = None
    if exact is not None:
        return deepcopy(exact)

    lowered = symbol.lower()
    for document in iter_collection_documents(collection):
        if str(document.get("current_symbol", "")).strip().lower() == lowered:
            return deepcopy(document)
    return None


def extract_cytology_tokens(*texts):
    tokens = []
    for text in texts:
        normalized = str(text or "").strip()
        if not normalized:
            continue
        for token in CYTOLOGY_TOKEN_RE.findall(normalized):
            if token not in tokens:
                tokens.append(token.upper())
    return tokens


def cytology_order(token):
    match = CYTOLOGY_POINT_RE.match(str(token or "").strip().upper())
    if match is None:
        return None
    band = int(match.group("band"))
    letter = ord(match.group("letter")) - ord("A")
    subband = int(match.group("subband") or "0")
    return band * 100 + letter * 10 + subband


def chromosome_arm_from_band(band_number):
    if 1 <= band_number <= 20:
        return {"arm": "X", "orientation": "left"}
    if 21 <= band_number <= 40:
        return {"arm": "2L", "orientation": "left"}
    if 41 <= band_number <= 60:
        return {"arm": "2R", "orientation": "right"}
    if 61 <= band_number <= 80:
        return {"arm": "3L", "orientation": "left"}
    if 81 <= band_number <= 100:
        return {"arm": "3R", "orientation": "right"}
    if 101 <= band_number <= 120:
        return {"arm": "4", "orientation": "left"}
    return {"arm": "unknown", "orientation": "left"}


def parse_cytology_point(token):
    normalized = str(token or "").strip().upper()
    match = CYTOLOGY_POINT_RE.match(normalized)
    if match is None:
        return None

    band = int(match.group("band"))
    arm_info = chromosome_arm_from_band(band)
    return {
        "token": normalized,
        "band": band,
        "order": cytology_order(normalized),
        "arm": arm_info["arm"],
        "orientation": arm_info["orientation"],
    }


def parse_cytology_position(text):
    tokens = extract_cytology_tokens(text)
    if not tokens:
        return None
    points = [point for point in (parse_cytology_point(token) for token in tokens) if point is not None]
    if not points:
        return None

    points.sort(key=lambda point: point["order"])
    start = points[0]
    end = points[-1]
    midpoint_order = (start["order"] + end["order"]) / 2
    return {
        "raw": str(text or "").strip(),
        "tokens": tokens,
        "points": points,
        "start": start,
        "end": end,
        "order": midpoint_order,
        "arm": start["arm"] if start["arm"] == end["arm"] else start["arm"],
        "orientation": start["orientation"],
    }


def is_distal_to_gene(gene_position, breakpoint_point):
    if gene_position is None or breakpoint_point is None:
        return False
    if gene_position["arm"] != breakpoint_point["arm"]:
        return False
    if gene_position["orientation"] == "right":
        return breakpoint_point["order"] > gene_position["order"]
    return breakpoint_point["order"] < gene_position["order"]


def flatten_available_stock_genotypes(available_stock_genotypes):
    packages = set()
    for stock in available_stock_genotypes or []:
        genotype = stock.get("Genotype") if isinstance(stock, dict) else stock
        if not genotype:
            continue
        try:
            normalized = normalize_genotype(genotype)
        except ValueError:
            continue
        for package_info in iter_genotype_packages(normalized, "female"):
            packages.add(package_info["package"])
    return packages