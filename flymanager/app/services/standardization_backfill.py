"""Bulk backfill of the materialized ``StandardizationCache`` field.

Mirrors ``flymanager.utils.phenotypes.backfill`` but for the standardization
review summary, reusing the shared ``backfill_materialized_cache`` machinery so
the two caches stay consistent in behaviour (user filtering, dry-run, force,
version/signature-based staleness detection).
"""

from flymanager.app.services.stock_standardization import (
    build_cross_standardization_cache, build_stock_standardization_cache,
    get_cached_cross_standardization, get_cached_stock_standardization)
from flymanager.utils.materialized_cache import backfill_materialized_cache


def backfill_stock_standardization_cache(collection, *, users=None, dry_run=False, force=False):
    return backfill_materialized_cache(
        collection,
        cache_field="StandardizationCache",
        cache_getter=get_cached_stock_standardization,
        cache_builder=lambda record: build_stock_standardization_cache(record.get("Genotype", "")),
        projection={"_id": 1, "User": 1, "AssignedTo": 1, "Genotype": 1, "StandardizationCache": 1},
        users=users,
        dry_run=dry_run,
        force=force,
    )


def backfill_cross_standardization_cache(collection, *, users=None, dry_run=False, force=False):
    return backfill_materialized_cache(
        collection,
        cache_field="StandardizationCache",
        cache_getter=get_cached_cross_standardization,
        cache_builder=lambda record: build_cross_standardization_cache(
            record.get("MaleGenotype", ""),
            record.get("FemaleGenotype", ""),
        ),
        projection={
            "_id": 1,
            "User": 1,
            "AssignedTo": 1,
            "MaleGenotype": 1,
            "FemaleGenotype": 1,
            "StandardizationCache": 1,
        },
        users=users,
        dry_run=dry_run,
        force=force,
    )
