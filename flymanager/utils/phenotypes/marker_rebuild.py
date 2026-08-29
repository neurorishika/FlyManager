"""Scoping and execution of materialized-cache rebuilds after a marker edit.

Any marker write moves the catalog signature, which makes every stored
prediction stale. Recomputing the whole collection for a one-line text tweak
is not acceptable, so a write rebuilds only the records whose genotype can
actually be affected, then stamps the new signature onto the rest.
"""
import re

from pymongo import UpdateOne

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


def _flybase_alias_tokens(key):
    """Genotype spellings the FlyBase evidence index resolves to this key.

    resolver.py falls back to a second alias mechanism beyond the catalog's
    own alias rows: a FlyBase-derived index mapping a bare allele spec
    ("bc") to a canonical token ("PPO1[Bc]"), which is then hydrated from the
    catalog. A genotype using the bare spelling therefore depends on this
    catalog row, and editing the row must sweep that spelling too.

    Read-only and in-process (the evidence cache is memoized), so this adds
    no database access. A missing or unreadable cache yields no extra
    tokens rather than raising: this runs on the marker write path, and
    failing a save because reference data is absent would be worse than
    over-rebuilding, which the caller can always force.
    """
    from flymanager.utils.phenotypes.flybase_pipeline import \
        get_flybase_phenotype_cache

    try:
        index = get_flybase_phenotype_cache().get("marker_alias_index") or {}
    except Exception:
        return set()

    return {
        alias
        for alias, record in index.items()
        if str((record or {}).get("canonical_token") or "") == key
    }


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
        # Keyed by geneStem, deliberately: a construct marker is built from the
        # gene marker of the same stem, so editing gene marker w changes every
        # P{...}w[+mC] in the collection. Those live inside construct bodies and
        # cannot be scoped by token, so this is a correct full rebuild, not a
        # missed optimization.
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
        # Second, independent alias mechanism: resolver.py also resolves a
        # bare allele spec through the FlyBase evidence index straight into
        # this key. Its keys are lowercase-normalised, which is fine because
        # build_affected_query matches case-insensitively.
        tokens.update(_flybase_alias_tokens(key))
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


STOCK_GENOTYPE_FIELDS = ("Genotype",)
CROSS_GENOTYPE_FIELDS = ("MaleGenotype", "FemaleGenotype")


def stamp_current_caches(collection, *, cache_field, cache_getter, signature, query=None):
    """Write the new catalog signature onto caches that are otherwise current.

    A record is stamped only when ``cache_getter(record, strict=True)`` still
    accepts it once the signature clause is ignored -- i.e. it is current on
    version, pipeline signature and genotype. Records with no cache, or stale
    for any other reason, are left for the next backfill.

    This is what makes targeting worthwhile: without it the next force=False
    backfill would recompute the whole collection anyway. The tradeoff is that
    a bug in derive_affected_tokens becomes permanently invisible here rather
    than self-correcting; the guarded force-recompute is the escape hatch.
    """
    operations = []
    for record in collection.find(query or {}):
        cache = record.get(cache_field)
        if not isinstance(cache, dict):
            continue
        if cache.get("markerCatalogSignature") == signature:
            # Already current -- typically a record the rebuild just wrote.
            continue
        probe = dict(record)
        probe[cache_field] = dict(cache, markerCatalogSignature=signature)
        if cache_getter(probe, strict=True) is None:
            continue
        operations.append(UpdateOne(
            {"_id": record["_id"]},
            {"$set": {f"{cache_field}.markerCatalogSignature": signature}},
        ))

    if operations:
        collection.bulk_write(operations, ordered=False)
    return len(operations)


def rebuild_after_marker_change(db, keys, *, previous_documents=(), deleted_override_keys=()):
    """Recompute the caches a marker edit can affect, then stamp the rest.

    Runs after the catalog snapshot has already been refreshed, so
    get_catalog()["signature"] is the post-edit signature.
    """
    from flymanager.app.services.standardization_backfill import (
        backfill_cross_standardization_cache,
        backfill_stock_standardization_cache)
    from flymanager.app.services.stock_standardization import (
        get_cached_cross_standardization, get_cached_stock_standardization)
    from flymanager.utils.phenotypes.backfill import (
        backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)
    from flymanager.utils.phenotypes.marker_catalog import get_catalog
    from flymanager.utils.phenotypes.predictor import (
        get_cached_cross_phenotype, get_cached_stock_phenotype)

    snapshot = get_catalog()
    signature = snapshot["signature"]
    tokens = derive_affected_tokens(snapshot, keys,
                                    previous_documents=previous_documents,
                                    deleted_override_keys=deleted_override_keys)

    result = {
        "scope": "full" if tokens is None else "targeted",
        "tokens": sorted(tokens) if tokens is not None else None,
    }

    plans = (
        ("stocks", STOCK_GENOTYPE_FIELDS,
         backfill_stock_phenotype_cache, get_cached_stock_phenotype,
         backfill_stock_standardization_cache, get_cached_stock_standardization),
        ("crosses", CROSS_GENOTYPE_FIELDS,
         backfill_cross_phenotype_cache, get_cached_cross_phenotype,
         backfill_cross_standardization_cache, get_cached_cross_standardization),
    )

    for (name, fields, phenotype_backfill, phenotype_getter,
         standardization_backfill, standardization_getter) in plans:
        collection = db[name]
        query = None if tokens is None else build_affected_query(tokens, genotype_fields=fields)

        rebuilt = 0
        if tokens is None or query is not None:
            phenotype_summary = phenotype_backfill(collection, query=query, force=True)
            standardization_backfill(collection, query=query, force=True)
            rebuilt = phenotype_summary["updated"]

        stamped = stamp_current_caches(
            collection, cache_field="PhenotypeCache",
            cache_getter=phenotype_getter, signature=signature,
        )
        stamp_current_caches(
            collection, cache_field="StandardizationCache",
            cache_getter=standardization_getter, signature=signature,
        )
        result[name] = {"rebuilt": rebuilt, "stamped": stamped}

    return result
