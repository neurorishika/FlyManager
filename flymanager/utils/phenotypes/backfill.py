from flymanager.utils.phenotypes.predictor import (build_cross_phenotype_cache,
                                                   build_stock_phenotype_cache,
                                                   get_cached_cross_phenotype,
                                                   get_cached_stock_phenotype)


def _normalize_users(users):
    if not users:
        return []

    normalized_users = []
    for user in users:
        normalized_user = str(user or "").strip()
        if normalized_user:
            normalized_users.append(normalized_user)
    return normalized_users


def _build_user_query(users):
    normalized_users = _normalize_users(users)
    if not normalized_users:
        return {}
    return {
        "$or": [
            {"User": {"$in": normalized_users}},
            {"AssignedTo": {"$in": normalized_users}},
        ]
    }


def _record_matches_users(record, users):
    normalized_users = set(_normalize_users(users))
    if not normalized_users:
        return True

    owner = str(record.get("User") or "").strip()
    assigned_to = str(record.get("AssignedTo") or "").strip()
    maintainer = assigned_to or owner
    return maintainer in normalized_users


def _backfill_collection(collection, *, query, projection, users=None, cache_getter, cache_builder,
                         cache_selector_builder, dry_run=False, force=False):
    summary = {
        "scanned": 0,
        "updated": 0,
        "skipped_valid": 0,
    }

    for record in collection.find(query, projection):
        if not _record_matches_users(record, users):
            continue
        summary["scanned"] += 1

        if not force and cache_getter(record):
            summary["skipped_valid"] += 1
            continue

        summary["updated"] += 1
        if dry_run:
            continue

        collection.update_one(
            cache_selector_builder(record),
            {"$set": {"PhenotypeCache": cache_builder(record)}},
        )

    return summary


def backfill_stock_phenotype_cache(collection, *, users=None, dry_run=False, force=False):
    return _backfill_collection(
        collection,
        query=_build_user_query(users),
        projection={"_id": 1, "User": 1, "AssignedTo": 1, "Genotype": 1, "PhenotypeCache": 1},
        users=users,
        cache_getter=get_cached_stock_phenotype,
        cache_builder=lambda record: build_stock_phenotype_cache(record.get("Genotype", "")),
        cache_selector_builder=lambda record: {"_id": record["_id"]},
        dry_run=dry_run,
        force=force,
    )


def backfill_cross_phenotype_cache(collection, *, users=None, dry_run=False, force=False):
    return _backfill_collection(
        collection,
        query=_build_user_query(users),
        projection={
            "_id": 1,
            "User": 1,
            "AssignedTo": 1,
            "MaleGenotype": 1,
            "FemaleGenotype": 1,
            "PhenotypeCache": 1,
        },
        users=users,
        cache_getter=get_cached_cross_phenotype,
        cache_builder=lambda record: build_cross_phenotype_cache(
            record.get("MaleGenotype", ""),
            record.get("FemaleGenotype", ""),
        ),
        cache_selector_builder=lambda record: {"_id": record["_id"]},
        dry_run=dry_run,
        force=force,
    )