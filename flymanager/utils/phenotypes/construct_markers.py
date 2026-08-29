import re

from flymanager.utils.phenotypes.marker_catalog import get_catalog
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

CONSTRUCT_PREFIX_RE = re.compile(r"^(?P<kind>[A-Za-z0-9]+)\{")


def construct_marker_pattern():
    """Regex matching any catalogued construct-marker stem plus its allele prefix.

    Built per call from the catalog rather than frozen at import, so a
    user-defined construct marker is matched as soon as it is saved. Stems are
    sorted longest-first so a longer stem is never shadowed by a prefix of it.
    """
    entries = get_catalog()["construct_markers"]
    if not entries:
        return None
    # Longest-first with a lexicographic tie-break for equal-length stems, so
    # iteration order (a dict, not guaranteed stable across catalog rebuilds)
    # can never flip which of two same-length stems matches first. No stems
    # are the same length today, so this has no behavioural effect yet -- see
    # marker_catalog.balancer_match_order for the same fix applied earlier on
    # this branch.
    alternatives = "|".join(
        re.escape(stem) for stem in sorted(entries, key=lambda s: (-len(s), s)) if stem
    )
    if not alternatives:
        return None
    return re.compile(rf"(?P<stem>{alternatives})\[(?P<allele>\+[^\]]*)\]")


def extract_construct_marker_symbols(construct_token):
    pattern = construct_marker_pattern()
    if pattern is None:
        return []

    markers = []
    seen = set()
    for match in pattern.finditer(construct_token or ""):
        symbol = match.group("stem")
        allele = match.group("allele")
        key = (symbol, allele)
        if key in seen:
            continue
        seen.add(key)
        markers.append({"gene_stem": symbol, "allele_spec": allele})
    return markers


def extract_construct_markers(construct_token):
    construct_markers = []
    entries = get_catalog()["construct_markers"]
    construct_type_match = CONSTRUCT_PREFIX_RE.match(construct_token or "")
    construct_type = construct_type_match.group("kind") if construct_type_match else "construct"

    for marker in extract_construct_marker_symbols(construct_token):
        stem = marker["gene_stem"]
        allele_spec = marker["allele_spec"]
        base_marker = get_visual_marker(stem)
        if base_marker is None:
            continue

        overrides = (entries.get(stem) or {}).get("overrides") or {}
        if overrides:
            base_marker.update(overrides)

        base_marker.update(
            {
                "allele_spec": allele_spec,
                "construct_token": construct_token,
                "construct_type": construct_type,
                "source": "construct_marker",
            }
        )
        construct_markers.append(base_marker)

    return construct_markers
