"""Asynchronous materialization of stock and cross derived caches.

The phenotype and standardization payloads are deliberately materialized in
the RQ worker.  Request handlers only persist a small, explicit state marker;
ordinary read paths must therefore treat a missing cache as a normal pending
condition rather than attempting to rebuild it.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

CACHE_GENERATION_VERSION = 1
logger = logging.getLogger(__name__)


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _signature(record_type, *genotypes):
    payload = "\x1f".join([str(CACHE_GENERATION_VERSION), record_type, *map(str, genotypes)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def stock_cache_signature(genotype):
    return _signature("stock", genotype or "")


def cross_cache_signature(male_genotype, female_genotype):
    return _signature("cross", male_genotype or "", female_genotype or "")


def cache_job_key(record_type, unique_id, input_signature):
    """Stable per-record/per-input key; active retries collapse in RQ/Mongo."""
    return f"cache-generation:{record_type}:{unique_id}:{input_signature}"


def pending_cache_generation(record_type, unique_id, input_signature):
    job_key = cache_job_key(record_type, unique_id, input_signature)
    return {
        "version": CACHE_GENERATION_VERSION,
        "state": "pending",
        "inputSignature": input_signature,
        "jobKey": job_key,
        "requestedAt": _timestamp(),
        "error": None,
        "phenotype": {"state": "pending"},
        "standardization": {"state": "pending"},
    }


def enqueue_record_cache_generation(db, *, actor, record_type, unique_id, input_signature):
    """Queue materialization after a record has committed.

    A queue outage intentionally does not roll back the stock/cross mutation.
    The persisted ``pending`` state makes it safe for an operator or a retry to
    enqueue the exact same deterministic operation later.
    """
    from flymanager.app.jobs import enqueue_job
    from flymanager.app.jobs.tasks import task_materialize_record_caches

    key = cache_job_key(record_type, unique_id, input_signature)
    try:
        enqueue_job(
            db,
            key=key,
            actor=actor,
            label=f"Generate {record_type} phenotype and standardization caches",
            func=task_materialize_record_caches,
            args=(key, record_type, unique_id, actor, input_signature),
            metadata={
                "recordType": record_type,
                "uniqueID": unique_id,
                "inputSignature": input_signature,
            },
            conflict_message="Cache generation is already queued for this record.",
        )
    except Exception as exc:
        if exc.__class__.__name__ == "OperationLockConflict":
            # The active job is the desired idempotent result.
            return key
        raise
    return key


def mark_cache_generation_enqueue_failed(db, *, record_type, unique_id, input_signature, error):
    """Expose Redis/RQ enqueue failure on the record without failing its write."""
    db[f"{record_type}es" if record_type == "cross" else "stocks"].update_one(
        {
            "UniqueID": unique_id,
            "CacheGeneration.inputSignature": input_signature,
        },
        {"$set": {
            "CacheGeneration.state": "failed",
            "CacheGeneration.error": str(error),
            "CacheGeneration.failedAt": _timestamp(),
            "CacheGeneration.phenotype.state": "failed",
            "CacheGeneration.standardization.state": "failed",
        }},
    )


def schedule_record_cache_generation(db, *, actor, record_type, unique_id, input_signature):
    """Best-effort post-commit dispatch that never changes mutation success."""
    try:
        job_key = cache_job_key(record_type, unique_id, input_signature)
        collection_name = "stocks" if record_type == "stock" else "crosses"
        db[collection_name].update_one(
            {
                "UniqueID": unique_id,
                "CacheGeneration.inputSignature": input_signature,
            },
            {"$set": {
                "CacheGeneration.state": "pending",
                "CacheGeneration.error": None,
                "CacheGeneration.jobKey": job_key,
                "CacheGeneration.requestedAt": _timestamp(),
                "CacheGeneration.phenotype.state": "pending",
                "CacheGeneration.standardization.state": "pending",
            }},
        )
        return enqueue_record_cache_generation(
            db,
            actor=actor,
            record_type=record_type,
            unique_id=unique_id,
            input_signature=input_signature,
        )
    except Exception as exc:
        logger.exception(
            "Could not enqueue cache generation for %s %s", record_type, unique_id,
        )
        try:
            mark_cache_generation_enqueue_failed(
                db,
                record_type=record_type,
                unique_id=unique_id,
                input_signature=input_signature,
                error=exc,
            )
        except Exception:
            # The primary mutation has already committed.  Never turn a
            # best-effort status update into a false form-submission failure.
            logger.exception(
                "Could not record cache-generation enqueue failure for %s %s",
                record_type, unique_id,
            )
        return None


def materialize_record_caches(db, *, record_type, unique_id, actor, input_signature):
    """Build and atomically publish derived fields if the input is still current.

    An older job may run after a genotype edit.  Its conditional update then
    affects no documents, preventing it from overwriting the newer pending
    generation state or cache.
    """
    collection_name = "stocks" if record_type == "stock" else "crosses"
    collection = db[collection_name]
    record = collection.find_one({"UniqueID": unique_id, "User": actor})
    if not record:
        return {"status": "missing"}

    if record_type == "stock":
        genotype = str(record.get("Genotype", ""))
        if stock_cache_signature(genotype) != input_signature:
            return {"status": "superseded"}
        from flymanager.app.services.stock_standardization import build_stock_standardization_cache
        from flymanager.utils.phenotypes.predictor import build_stock_phenotype_cache
        phenotype_cache = build_stock_phenotype_cache(genotype)
        standardization_cache = build_stock_standardization_cache(genotype)
    elif record_type == "cross":
        male_genotype = str(record.get("MaleGenotype", ""))
        female_genotype = str(record.get("FemaleGenotype", ""))
        if cross_cache_signature(male_genotype, female_genotype) != input_signature:
            return {"status": "superseded"}
        from flymanager.app.services.stock_standardization import build_cross_standardization_cache
        from flymanager.utils.phenotypes.predictor import build_cross_phenotype_cache
        phenotype_cache = build_cross_phenotype_cache(male_genotype, female_genotype)
        standardization_cache = build_cross_standardization_cache(male_genotype, female_genotype)
    else:
        raise ValueError(f"Unsupported record type: {record_type}")

    result = collection.update_one(
        {
            "UniqueID": unique_id,
            "User": actor,
            "CacheGeneration.inputSignature": input_signature,
        },
        {"$set": {
            "PhenotypeCache": phenotype_cache,
            "StandardizationCache": standardization_cache,
            "CacheGeneration.state": "ready",
            "CacheGeneration.error": None,
            "CacheGeneration.completedAt": _timestamp(),
            "CacheGeneration.phenotype.state": "ready",
            "CacheGeneration.standardization.state": "ready",
        }},
    )
    return {"status": "ready" if result.matched_count else "superseded"}


def fail_record_cache_generation(db, *, record_type, unique_id, actor, input_signature, error):
    collection_name = "stocks" if record_type == "stock" else "crosses"
    db[collection_name].update_one(
        {
            "UniqueID": unique_id,
            "User": actor,
            "CacheGeneration.inputSignature": input_signature,
        },
        {"$set": {
            "CacheGeneration.state": "failed",
            "CacheGeneration.error": str(error),
            "CacheGeneration.failedAt": _timestamp(),
            "CacheGeneration.phenotype.state": "failed",
            "CacheGeneration.standardization.state": "failed",
        }},
    )
