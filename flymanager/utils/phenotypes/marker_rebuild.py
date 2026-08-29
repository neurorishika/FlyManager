"""Scoping and execution of materialized-cache rebuilds after a marker edit.

Any marker write moves the catalog signature, which makes every stored
prediction stale. Recomputing the whole collection for a one-line text tweak
is not acceptable, so a write rebuilds only the records whose genotype can
actually be affected, then stamps the new signature onto the rest.
"""
import re

UNSCOPABLE_KINDS = ("construct_marker",)


def _own_tokens(document):
    """Tokens by which a definition can appear in a genotype, from the
    document alone -- no snapshot needed, so this also works for a pre-edit
    document whose values no longer exist in the current catalog."""
    match = (document or {}).get("match") or {}
    payload = (document or {}).get("payload") or {}
    tokens = {str(document.get("Key") or ""), str(match.get("symbol") or ""),
              str(match.get("token") or ""), str(payload.get("value") or "")}
    tokens.update(str(alias) for alias in (match.get("aliases") or []))
    return {token for token in tokens if token}


def derive_affected_tokens(snapshot, keys, *, previous_documents=(), deleted_override_keys=()):
    """Genotype substrings whose records must be recomputed for these key edits.

    Returns None when the change cannot be scoped and the caller must rebuild
    everything. Two cases:

    * ``construct_marker`` edits, because the match is a pattern *inside*
      construct bodies (``P{...}w[+mC]``), not a whole token we can search for.
    * Deleting an override of a shipped key, because the restored shipped
      definition's blast radius is not derivable from the deleted row alone.
    """
    keys = [str(key or "").strip() for key in keys or []]
    keys = [key for key in keys if key]
    if deleted_override_keys:
        return None

    definitions = snapshot.get("definitions") or {}
    for key in keys:
        definition = definitions.get(key)
        if definition is not None and definition.get("kind") in UNSCOPABLE_KINDS:
            return None
        if key in (snapshot.get("construct_markers") or {}):
            return None

    tokens = set()
    # Pre-edit documents first: a removed alias or a renamed symbol exists
    # nowhere in the post-edit snapshot, so only the old document can tell us
    # which genotypes just stopped resolving the way they used to.
    for document in previous_documents or []:
        if (document or {}).get("kind") in UNSCOPABLE_KINDS:
            return None
        tokens.update(_own_tokens(document))

    balancers = snapshot.get("balancers") or {}
    balancers_referencing = snapshot.get("balancers_referencing") or {}
    aliases_by_target = snapshot.get("aliases_by_target") or {}

    for key in keys:
        tokens.add(key)

        # Aliases that resolve *to* this key, and the target this key
        # resolves to, both change what a genotype containing either means.
        tokens.update(aliases_by_target.get(key, set()))
        definition = definitions.get(key)
        if definition is not None and definition.get("kind") == "alias":
            target = str((definition.get("payload") or {}).get("value") or "").strip()
            if target:
                tokens.add(target)

        # Reverse references: a balancer resolves its markers by key, so
        # editing a marker changes every balancer that lists it -- and those
        # balancers' aliases, which are what actually appear in genotypes.
        for symbol in balancers_referencing.get(key, set()):
            tokens.add(symbol)
            tokens.update((balancers.get(symbol) or {}).get("aliases") or [])

        # Editing a balancer sweeps its own aliases, but not its markers:
        # the markers themselves did not change.
        if definition is not None and definition.get("kind") == "balancer":
            symbol = str((definition.get("match") or {}).get("symbol") or key)
            tokens.add(symbol)
            tokens.update((balancers.get(symbol) or {}).get("aliases") or [])

    return tokens


def build_affected_query(tokens, *, genotype_fields):
    """A Mongo query matching records whose genotype contains any token.

    Substring matching over-matches on purpose: editing ``B`` sweeps most of
    the collection. Extra rebuilds cost time; missed rebuilds are silently
    wrong predictions. Returns None when there is nothing to match.
    """
    tokens = sorted(token for token in (tokens or set()) if token)
    if not tokens:
        return None

    pattern = "|".join(re.escape(token) for token in tokens)
    # Case-insensitive on purpose: the resolver reaches markers through the
    # lowercase-normalised FlyBase alias index, so a genotype spelling of
    # "sco" resolves to sna[Sco] and must be swept when that row is edited.
    return {"$or": [{field: {"$regex": pattern, "$options": "i"}}
                    for field in genotype_fields]}
