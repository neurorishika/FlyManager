from flymanager.utils.materialized_cache import (backfill_materialized_cache,
                                                 build_user_query,
                                                 normalize_users,
                                                 record_matches_users)
from flymanager.utils.phenotypes.predictor import (build_cross_phenotype_cache,
                                                   build_stock_phenotype_cache,
                                                   get_cached_cross_phenotype,
                                                   get_cached_stock_phenotype)

# Backwards-compatible aliases for the helpers that used to live here.
_normalize_users = normalize_users
_build_user_query = build_user_query
_record_matches_users = record_matches_users


def backfill_stock_phenotype_cache(collection, *, users=None, query=None, dry_run=False, force=False):
    return backfill_materialized_cache(
        collection,
        cache_field="PhenotypeCache",
        cache_getter=get_cached_stock_phenotype,
        cache_builder=lambda record: build_stock_phenotype_cache(record.get("Genotype", "")),
        projection={"_id": 1, "User": 1, "AssignedTo": 1, "Genotype": 1, "PhenotypeCache": 1},
        users=users,
        query=query,
        dry_run=dry_run,
        force=force,
    )


def backfill_cross_phenotype_cache(collection, *, users=None, query=None, dry_run=False, force=False):
    return backfill_materialized_cache(
        collection,
        cache_field="PhenotypeCache",
        cache_getter=get_cached_cross_phenotype,
        cache_builder=lambda record: build_cross_phenotype_cache(
            record.get("MaleGenotype", ""),
            record.get("FemaleGenotype", ""),
        ),
        projection={
            "_id": 1,
            "User": 1,
            "AssignedTo": 1,
            "MaleGenotype": 1,
            "FemaleGenotype": 1,
            "PhenotypeCache": 1,
        },
        users=users,
        query=query,
        dry_run=dry_run,
        force=force,
    )
