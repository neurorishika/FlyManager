"""Resolve a marker catalog Key into the marker dicts the scorer speaks.

Everything downstream of the predictor -- image scoring especially -- works
on resolved marker dicts (`phenotype_key`, `display_label`, `gene_stem`,
`body_part`, ...). A catalog Key is not one, and each kind reaches its marker
dict differently. Callers that only have a Key, such as the marker catalog
page, would otherwise each reinvent this per kind and drift.

A balancer is deliberately not a marker: it carries markers. Scoring a
balancer directly returns nothing, because its metadata holds none of the
fields the scorer reads, which is why it resolves to one group per carried
marker instead.
"""
import re

from flymanager.utils.phenotypes.marker_catalog import get_catalog
from flymanager.utils.phenotypes.visual_markers import get_visual_marker

# `Orco-LexA -> P{Orco-LexA-VP16}unspecified` has no gene stem to fall back
# to; `w- -> w[*]` does. This is the shape of an allele-ish token.
_STEM_RE = re.compile(r"^([A-Za-z0-9_]+)\[")


def _group(marker_key, marker, fallback_label=None):
    label = ""
    if marker:
        label = str(marker.get("display_label") or "")
    return {"marker_key": marker_key,
            "display_label": label or fallback_label or marker_key,
            "marker": marker}


def _resolve_one(key, catalog, _following_alias=False):
    """One group for a non-balancer Key, or None if the Key is unknown."""
    document = (catalog.get("definitions") or {}).get(key)
    if document is None:
        return None
    kind = document.get("kind")

    if kind in ("gene_marker", "allele_marker"):
        match = document.get("match") or {}
        marker = get_visual_marker(
            match.get("symbol") or match.get("geneStem") or key,
            allele_spec=match.get("alleleSpec"),
            token=key if kind == "allele_marker" else None)
        return _group(key, marker, fallback_label=key)

    if kind == "construct_marker":
        stem = str((document.get("match") or {}).get("geneStem") or "")
        construct = (catalog.get("construct_markers") or {}).get(stem)
        if construct is None:
            return _group(key, None, fallback_label=key)
        marker = dict(get_visual_marker(stem) or {})
        marker.update(construct.get("overrides") or {})
        marker.setdefault("gene_stem", stem)
        return _group(key, marker, fallback_label=key)

    if kind == "alias":
        target = str((document.get("payload") or {}).get("value") or "")
        if _following_alias:
            # An alias chain is a data error, not a feature. Follow one hop so
            # the common `Gla -> wg[Gla-1]` case works, then stop rather than
            # risk a cycle.
            return _group(target or key, None, fallback_label=target or key)
        resolved = _resolve_one(target, catalog, _following_alias=True)
        if resolved is not None:
            return resolved
        # Three shipped aliases point at something that is not a definition
        # Key: `w- -> w[*]` and two Orco-LexA spellings. Fall back to the
        # target's gene stem, which rescues `w[*] -> w` (a real gene marker
        # with white-eye photos) and honestly finds nothing for the transgene.
        stem_match = _STEM_RE.match(target)
        if stem_match:
            marker = get_visual_marker(stem_match.group(1))
            if marker is not None:
                return _group(stem_match.group(1), marker)
        return _group(target or key, None, fallback_label=target or key)

    return _group(key, None, fallback_label=key)


def resolve_definition_markers(definition_key):
    """Groups of resolved markers for a catalog Key.

    One group for every kind except `balancer`, which yields one per carried
    marker. A group whose `marker` is None did not resolve; callers must
    render it as "nothing found" rather than dropping it, or a balancer
    silently under-reports what it carries.
    """
    key = str(definition_key or "").strip()
    catalog = get_catalog()
    document = (catalog.get("definitions") or {}).get(key)
    if document is None:
        return []

    if document.get("kind") == "balancer":
        symbol = str((document.get("match") or {}).get("symbol") or key)
        carried = (catalog.get("balancer_markers") or {}).get(symbol) or []
        groups = []
        for marker_key in carried:
            resolved = _resolve_one(marker_key, catalog)
            groups.append(resolved or _group(marker_key, None,
                                             fallback_label=marker_key))
        return groups

    resolved = _resolve_one(key, catalog)
    return [resolved] if resolved else []
