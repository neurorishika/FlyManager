"""Reusable helpers for materialized, document-embedded derived caches.

FlyManager stores several kinds of derived data directly on stock/cross
documents (``PhenotypeCache``, ``StandardizationCache``, ...) so that read-heavy
pages never recompute expensive pipelines on the fly. Every such cache follows
the same lifecycle:

* it is (re)built on write whenever the source genotype changes;
* it carries a ``version`` and a reference-data ``pipelineSignature`` so stale
  entries can be detected and recomputed;
* it is backfilled in bulk when reference data is refreshed or on demand.

This module centralises the bulk-backfill machinery so each cache only has to
supply its field name, a ``cache_getter`` (returns the still-valid cache or
``None``) and a ``cache_builder`` (recomputes it). Keeping this generic means a
new materialized cache is a handful of lines, not a copied backfill loop.
"""

import logging

from pymongo import UpdateOne

_BULK_WRITE_CHUNK_SIZE = 500

_logger = logging.getLogger(__name__)


def normalize_users(users):
    if not users:
        return []

    normalized_users = []
    for user in users:
        normalized_user = str(user or "").strip()
        if normalized_user:
            normalized_users.append(normalized_user)
    return normalized_users


def build_user_query(users):
    normalized_users = normalize_users(users)
    if not normalized_users:
        return {}
    return {
        "$or": [
            {"User": {"$in": normalized_users}},
            {"AssignedTo": {"$in": normalized_users}},
        ]
    }


def record_matches_users(record, users):
    normalized_users = set(normalize_users(users))
    if not normalized_users:
        return True

    owner = str(record.get("User") or "").strip()
    assigned_to = str(record.get("AssignedTo") or "").strip()
    maintainer = assigned_to or owner
    return maintainer in normalized_users


def _default_selector(record):
    return {"_id": record["_id"]}


def backfill_materialized_cache(collection, *, cache_field, cache_getter, cache_builder,
                                query=None, projection=None, users=None,
                                cache_selector_builder=_default_selector,
                                dry_run=False, force=False):
    """Recompute a materialized cache field across a collection.

    Skips records whose cache is already valid (``cache_getter`` returns a
    truthy value) unless ``force`` is set. Honours a ``users`` maintainer filter
    both at the query level and per-record. A ``cache_builder`` exception is
    caught, logged, and counted in ``errors``, and that record is left
    unmodified rather than aborting the run. Writes are batched via
    ``bulk_write`` in chunks of ``_BULK_WRITE_CHUNK_SIZE`` for efficiency.
    Returns a summary dict with ``scanned`` / ``updated`` / ``skipped_valid`` /
    ``errors`` counts.
    """
    if query is None:
        query = build_user_query(users)

    summary = {
        "scanned": 0,
        "updated": 0,
        "skipped_valid": 0,
        "errors": 0,
    }

    pending_operations = []

    def flush():
        if pending_operations:
            collection.bulk_write(list(pending_operations), ordered=False)
            pending_operations.clear()

    for record in collection.find(query, projection):
        if not record_matches_users(record, users):
            continue
        summary["scanned"] += 1

        if not force and cache_getter(record):
            summary["skipped_valid"] += 1
            continue

        if dry_run:
            summary["updated"] += 1
            continue

        try:
            cache_payload = cache_builder(record)
        except Exception:
            summary["errors"] += 1
            _logger.exception(
                "cache_builder failed while backfilling %s for record %s",
                cache_field, record.get("_id"),
            )
            continue

        summary["updated"] += 1
        pending_operations.append(
            UpdateOne(
                cache_selector_builder(record),
                {"$set": {cache_field: cache_payload}},
            )
        )
        if len(pending_operations) >= _BULK_WRITE_CHUNK_SIZE:
            flush()

    flush()
    return summary
