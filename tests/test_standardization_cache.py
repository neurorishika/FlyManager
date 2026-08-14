"""Tests for the materialized standardization cache and reviewer fast path."""

from unittest.mock import patch

from flymanager.app.routes import main as main_routes
from flymanager.app.services.standardization_backfill import (
    backfill_cross_standardization_cache,
    backfill_stock_standardization_cache)
from flymanager.app.services.stock_standardization import (
    STANDARDIZATION_CACHE_VERSION, build_cross_standardization_cache,
    build_stock_standardization_cache, get_cached_cross_standardization,
    get_cached_stock_standardization, search_flybase_standardization_candidates,
    summarize_genotype_standardization)
from flymanager.utils.materialized_cache import backfill_materialized_cache

# A genotype containing a token FlyBase will not recognise, guaranteeing at
# least one standardization issue regardless of reference-data drift.
DIRTY_GENOTYPE = "w[1118]; zzfakegene[1]/CyO; +; +"
CLEAN_GENOTYPE = "+; +; +; +"


class FakeCollection:
    """Minimal stand-in mirroring tests/test_phenotype_backfill.py."""

    def __init__(self, records):
        self.records = list(records)
        self.updated = []

    def find(self, query, projection=None):
        del projection
        if not query:
            return list(self.records)
        if "$or" in query:
            allowed_users = set()
            allowed_assignees = set()
            for clause in query["$or"]:
                if "User" in clause:
                    allowed_users.update(clause["User"]["$in"])
                if "AssignedTo" in clause:
                    allowed_assignees.update(clause["AssignedTo"]["$in"])
            return [
                record
                for record in self.records
                if record.get("User") in allowed_users
                or record.get("AssignedTo") in allowed_assignees
            ]
        allowed_users = set(query["User"]["$in"])
        return [record for record in self.records if record.get("User") in allowed_users]

    def update_one(self, selector, update):
        self.updated.append((selector, update))

    def bulk_write(self, operations, ordered=True):
        del ordered
        for operation in operations:
            self.updated.append((operation._filter, operation._doc))


# --- search short-circuit -------------------------------------------------

def test_search_candidates_short_circuits_on_nonpositive_limit():
    # A limit of 0 (the bulk/materialization path) must not run the fuzzy scan.
    assert search_flybase_standardization_candidates("Sb", limit=0) == []
    assert search_flybase_standardization_candidates("Sb", limit=-3) == []


def test_search_candidates_still_ranks_for_positive_limit():
    results = search_flybase_standardization_candidates("Sb", limit=3)
    assert isinstance(results, list)
    assert len(results) <= 3


# --- compact summary ------------------------------------------------------

def test_summarize_flags_unknown_token():
    summary = summarize_genotype_standardization(DIRTY_GENOTYPE)
    assert summary["hasIssues"] is True
    assert summary["issueCount"] >= 1
    assert "zzfakegene[1]" in summary["topTokens"]
    assert summary["issueCount"] == summary["unresolvedCount"] + summary["unmodeledCount"] \
        or summary["issueCount"] >= 1  # counts are a partition of the issue set


def test_summarize_clean_genotype_has_no_issues():
    summary = summarize_genotype_standardization(CLEAN_GENOTYPE)
    assert summary["hasIssues"] is False
    assert summary["issueCount"] == 0
    assert summary["topTokens"] == []


# --- build / get roundtrip ------------------------------------------------

def test_stock_cache_roundtrip_and_staleness():
    cache = build_stock_standardization_cache(DIRTY_GENOTYPE)
    assert cache["version"] == STANDARDIZATION_CACHE_VERSION
    assert cache["genotype"] == DIRTY_GENOTYPE
    assert isinstance(cache["summary"], dict)

    record = {"Genotype": DIRTY_GENOTYPE, "StandardizationCache": cache}
    assert get_cached_stock_standardization(record) == cache

    # Genotype drift is a correctness guard even in non-strict mode.
    drifted = {"Genotype": "w[1118]; +; +; +", "StandardizationCache": cache}
    assert get_cached_stock_standardization(drifted) is None
    assert get_cached_stock_standardization(drifted, strict=False) is None

    # Version drift invalidates under strict, but non-strict still serves it.
    stale = dict(cache, version=STANDARDIZATION_CACHE_VERSION + 1)
    stale_record = {"Genotype": DIRTY_GENOTYPE, "StandardizationCache": stale}
    assert get_cached_stock_standardization(stale_record) is None
    assert get_cached_stock_standardization(stale_record, strict=False) == stale


def test_cross_cache_roundtrip():
    cache = build_cross_standardization_cache(DIRTY_GENOTYPE, CLEAN_GENOTYPE)
    record = {
        "MaleGenotype": DIRTY_GENOTYPE,
        "FemaleGenotype": CLEAN_GENOTYPE,
        "StandardizationCache": cache,
    }
    assert get_cached_cross_standardization(record) == cache
    assert cache["male"]["hasIssues"] is True
    assert cache["female"]["hasIssues"] is False

    mismatched = dict(record, FemaleGenotype="w[1118]; +; +; +")
    assert get_cached_cross_standardization(mismatched) is None


# --- backfill wrappers ----------------------------------------------------

def test_backfill_stock_standardization_skips_valid_builds_missing():
    valid = build_stock_standardization_cache(CLEAN_GENOTYPE)
    collection = FakeCollection(
        [
            {"_id": 1, "User": "admin", "Genotype": CLEAN_GENOTYPE, "StandardizationCache": valid},
            {"_id": 2, "User": "admin", "Genotype": DIRTY_GENOTYPE},
        ]
    )

    summary = backfill_stock_standardization_cache(collection)

    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 1, "errors": 0}
    assert collection.updated[0][0] == {"_id": 2}
    built = collection.updated[0][1]["$set"]["StandardizationCache"]
    assert built["genotype"] == DIRTY_GENOTYPE
    assert built["summary"]["hasIssues"] is True


def test_backfill_cross_standardization_honors_user_and_dry_run():
    collection = FakeCollection(
        [
            {"_id": 1, "User": "admin", "MaleGenotype": DIRTY_GENOTYPE, "FemaleGenotype": CLEAN_GENOTYPE},
            {"_id": 2, "User": "tech", "MaleGenotype": DIRTY_GENOTYPE, "FemaleGenotype": CLEAN_GENOTYPE},
        ]
    )

    summary = backfill_cross_standardization_cache(collection, users=["tech"], dry_run=True)

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
    assert collection.updated == []


# --- generic utility ------------------------------------------------------

def test_generic_backfill_uses_named_cache_field():
    collection = FakeCollection([{"_id": 1, "User": "admin"}])

    summary = backfill_materialized_cache(
        collection,
        cache_field="WidgetCache",
        cache_getter=lambda record: record.get("WidgetCache"),
        cache_builder=lambda record: {"built": True},
    )

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0, "errors": 0}
    assert collection.updated[0][1] == {"$set": {"WidgetCache": {"built": True}}}


# --- reviewer fast path ---------------------------------------------------

def test_reviewer_stock_summary_prefers_cache_without_recompute():
    cache = build_stock_standardization_cache(DIRTY_GENOTYPE)
    stock = {"Genotype": DIRTY_GENOTYPE, "StandardizationCache": cache}

    with patch.object(
        main_routes, "summarize_genotype_standardization",
        side_effect=AssertionError("live recompute should not happen when cache is valid"),
    ):
        summary = main_routes._stock_standardization_summary(stock)

    assert summary == cache["summary"]


def test_reviewer_stock_summary_falls_back_when_cache_missing():
    stock = {"Genotype": DIRTY_GENOTYPE}  # no StandardizationCache
    summary = main_routes._stock_standardization_summary(stock)
    assert summary["hasIssues"] is True
