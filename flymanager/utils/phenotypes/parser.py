import re

from flymanager.utils.phenotypes.construct_markers import \
    extract_construct_marker_symbols
from flymanager.utils.phenotypes.visual_markers import (BALANCER_ALIASES,
                                                        BALANCER_MARKERS,
                                                        KNOWN_BALANCER_SYMBOLS,
                                                        get_balancer_metadata)

GROUP_PAIRS = {"{": "}", "[": "]", "(": ")"}
CONSTRUCT_PREFIXES = ("P{", "PBac{", "Mi{", "TI{", "M{")
ALLELE_RE = re.compile(r"^(?P<gene>[A-Za-z0-9.+*()_-]+)\[(?P<allele>[^\]]+)\]$")
KNOWN_BALANCERS = KNOWN_BALANCER_SYMBOLS
BALANCER_MATCH_ORDER = tuple(sorted(KNOWN_BALANCERS, key=len, reverse=True))


def tokenize_gene_package(package_str):
    text = (package_str or "").strip()
    if not text or text == "+":
        return []

    tokens = []
    current = []
    closing_stack = []

    for character in text:
        if character in GROUP_PAIRS:
            closing_stack.append(GROUP_PAIRS[character])
            current.append(character)
            continue

        if character in GROUP_PAIRS.values():
            if closing_stack and character == closing_stack[-1]:
                closing_stack.pop()
            current.append(character)
            continue

        if not closing_stack and character in {",", " "}:
            token = "".join(current).strip().rstrip(":")
            if token:
                tokens.append(token)
            current = []
            continue

        current.append(character)

    token = "".join(current).strip().rstrip(":")
    if token:
        tokens.append(token)

    return tokens


def _is_construct(token):
    return any(token.startswith(prefix) for prefix in CONSTRUCT_PREFIXES) and "}" in token


def _is_balancer(token):
    if token in KNOWN_BALANCERS:
        return True
    if token.startswith("In("):
        return any(symbol in token for symbol in BALANCER_MATCH_ORDER)
    return False


def _normalize_balancer_symbol(token):
    if token in BALANCER_ALIASES:
        return BALANCER_ALIASES[token]
    if token in KNOWN_BALANCERS:
        return token
    for symbol in BALANCER_MATCH_ORDER:
        if symbol in token:
            return BALANCER_ALIASES.get(symbol, symbol)
    return token


def _is_aberration(token):
    return token.startswith(("Df(", "Dp(", "Tp(", "T(", "C(", "In("))


def parse_gene_package(package_str):
    text = (package_str or "").strip()
    result = {
        "input": text,
        "is_wild_type": text in {"", "+"},
        "annotations": [],
        "raw_tokens": tokenize_gene_package(text),
        "constructs": [],
        "classical_alleles": [],
        "balancers": [],
        "aberrations": [],
        "unresolved": [],
    }

    for token in result["raw_tokens"]:
        if token.startswith("|") and token.endswith("|"):
            result["annotations"].append(token)
            continue

        if _is_construct(token):
            kind = token.split("{", 1)[0]
            result["constructs"].append(
                {
                    "full": token,
                    "type": kind,
                    "markers": extract_construct_marker_symbols(token),
                }
            )
            continue

        if _is_balancer(token):
            symbol = _normalize_balancer_symbol(token)
            metadata = get_balancer_metadata(symbol) or {}
            result["balancers"].append(
                {
                    "token": token,
                    "symbol": symbol,
                    "default_markers": metadata.get("default_markers", BALANCER_MARKERS.get(symbol, [])),
                    "metadata": metadata,
                }
            )
            continue

        allele_match = ALLELE_RE.match(token)
        if allele_match:
            result["classical_alleles"].append(
                {
                    "token": token,
                    "gene_stem": allele_match.group("gene"),
                    "allele_spec": allele_match.group("allele"),
                }
            )
            continue

        if _is_aberration(token):
            result["aberrations"].append(token)
            continue

        result["unresolved"].append(token)

    return result
