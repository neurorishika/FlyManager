import re

from flymanager.utils.phenotypes.construct_markers import \
    extract_construct_marker_symbols
from flymanager.utils.phenotypes.visual_markers import (get_balancer_aliases,
                                                        get_balancer_match_order,
                                                        get_balancer_metadata,
                                                        get_known_balancer_symbols)

GROUP_PAIRS = {"{": "}", "[": "]", "(": ")"}
CONSTRUCT_PREFIXES = ("P{", "PBac{", "Mi{", "TI{", "M{")
ALLELE_RE = re.compile(r"^(?P<gene>[A-Za-z0-9.+*()_-]+)\[(?P<allele>[^\]]+)\]$")


def known_balancer_symbols():
    """Balancer symbols and aliases from the current catalog snapshot.

    Recomputed per call rather than frozen at import: a user-defined balancer
    must be recognised without restarting the process.
    """
    return get_known_balancer_symbols()


def balancer_match_order():
    """Symbols longest-first, so In(2LR)SM6a matches SM6a before SM6.

    Read straight off the snapshot, which precomputes the ordering: this runs
    once per In(...) token and re-sorting 38 symbols each time was measurable
    during a full backfill. The precomputed tuple breaks length ties
    alphabetically so the order does not shift with PYTHONHASHSEED.
    """
    return get_balancer_match_order()


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
    if token in known_balancer_symbols():
        return True
    if token.startswith("In("):
        return any(symbol in token for symbol in balancer_match_order())
    return False


def _normalize_balancer_symbol(token):
    aliases = get_balancer_aliases()
    if token in aliases:
        return aliases[token]
    if token in known_balancer_symbols():
        return token
    for symbol in balancer_match_order():
        if symbol in token:
            return aliases.get(symbol, symbol)
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
                    "default_markers": list(metadata.get("default_markers", [])),
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
