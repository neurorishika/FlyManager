"""Marker accessors over the compiled catalog snapshot.

The marker dictionaries that used to be hardcoded in this file now live in
data/markers/catalog.json plus the marker_definitions Mongo overlay, compiled
by flymanager/utils/phenotypes/marker_catalog.py. This module keeps the three
legacy get_* accessors' exact signatures and return shapes, and adds accessors
for the structures that used to be exported as module-level dicts.

Nothing here touches Mongo: these are all reads of the in-process snapshot.
"""
from copy import deepcopy

from flymanager.utils.phenotypes.marker_catalog import get_catalog


def get_gene_marker_dictionary():
    """Copy of the gene-symbol -> marker mapping (was VISUAL_MARKER_DICTIONARY)."""
    return deepcopy(get_catalog()["gene_markers"])


def get_allele_marker_dictionary():
    """Copy of the allele-token -> marker mapping (was ALLELE_VISUAL_MARKER_DICTIONARY)."""
    return deepcopy(get_catalog()["allele_markers"])


def get_gene_marker_symbols():
    """Immutable set of known gene symbols.

    Returns the snapshot's precomputed frozenset rather than a fresh copy:
    membership tests are all the callers need, and this runs per token during
    a full backfill.
    """
    return get_catalog()["gene_marker_symbols"]


def get_allele_marker_tokens():
    """Immutable set of known allele tokens."""
    return get_catalog()["allele_marker_tokens"]


def get_reviewed_marker_aliases():
    """Copy of the reviewed alias table (was REVIEWED_MARKER_ALIASES)."""
    return deepcopy(get_catalog()["aliases"])


def get_balancer_metadata_map():
    """Copy of the balancer symbol -> metadata mapping (was BALANCER_METADATA)."""
    return deepcopy(get_catalog()["balancers"])


def get_balancer_aliases():
    """Copy of the balancer alias -> canonical symbol mapping."""
    return dict(get_catalog()["balancer_aliases"])


def get_balancer_markers():
    """Copy of the balancer symbol -> default marker keys mapping."""
    return deepcopy(get_catalog()["balancer_markers"])


def get_known_balancer_symbols():
    """Every balancer symbol and alias the parser should recognise."""
    return get_catalog()["known_balancer_symbols"]


def get_balancer_match_order():
    """Balancer symbols longest-first, precomputed on the snapshot."""
    return get_catalog()["balancer_match_order"]


def get_probe_marker_symbols():
    """Symbols flagged as FlyBase coverage probes (was CRITICAL_MARKERS)."""
    return list(get_catalog()["probe_symbols"])


def get_balancer_metadata(symbol):
    catalog = get_catalog()
    text = str(symbol or "").strip()
    canonical_symbol = catalog["balancer_aliases"].get(text, text)
    metadata = catalog["balancers"].get(canonical_symbol)
    if metadata is None:
        return None
    return deepcopy(metadata)


def get_visual_marker(symbol, allele_spec=None, token=None):
    catalog = get_catalog()
    allele_markers = catalog["allele_markers"]

    allele_key = None
    if token and token in allele_markers:
        allele_key = token
    elif symbol and allele_spec:
        candidate_key = f"{symbol}[{allele_spec}]"
        if candidate_key in allele_markers:
            allele_key = candidate_key

    if allele_key is not None:
        marker = deepcopy(allele_markers[allele_key])
        marker.setdefault("gene_stem", symbol)
        marker["allele_specific"] = True
        marker["allele_token"] = allele_key
        return marker

    entry = catalog["gene_markers"].get(symbol)
    if entry is None:
        return None
    marker = deepcopy(entry)
    marker["gene_stem"] = symbol
    return marker


def get_reviewed_marker_alias(alias_token):
    return deepcopy(get_catalog()["aliases"].get(str(alias_token or "").strip()))


