#!/usr/bin/env python3
"""Measure the cold derived-cache work using the deployed FlyBase data.

Run this *inside the production image* with the normal FlyBase volume mounted:

    python scripts/benchmark_derived_cache_cold_start.py --genotype 'w[*]; CyO/+'

It performs no Mongo writes.  The reported builder time is the work that the
old synchronous POST performed and that the RQ worker now performs instead.
"""
import argparse
import json
import platform
import resource
import time

from flymanager.app.services.stock_standardization import build_stock_standardization_cache
from flymanager.utils.phenotypes import flybase_pipeline
from flymanager.utils.phenotypes.marker_catalog import reset_catalog
from flymanager.utils.phenotypes.predictor import build_stock_phenotype_cache


def _rss_mib():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux (the production image) reports KiB.
    return value / (1024 * 1024) if platform.system() == "Darwin" else value / 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--genotype", required=True)
    args = parser.parse_args()

    flybase_pipeline._IN_MEMORY_CACHE.clear()
    reset_catalog()
    before_mib = _rss_mib()
    started_at = time.perf_counter()
    phenotype = build_stock_phenotype_cache(args.genotype)
    phenotype_ms = (time.perf_counter() - started_at) * 1000
    after_phenotype_mib = _rss_mib()

    started_at = time.perf_counter()
    standardization = build_stock_standardization_cache(args.genotype)
    standardization_ms = (time.perf_counter() - started_at) * 1000
    after_mib = _rss_mib()

    print(json.dumps({
        "genotype": args.genotype,
        "legacySynchronousWorkMs": round(phenotype_ms + standardization_ms, 1),
        "phenotypeBuilderMs": round(phenotype_ms, 1),
        "standardizationBuilderMs": round(standardization_ms, 1),
        "rssMiBBefore": round(before_mib, 1),
        "rssMiBAfterPhenotype": round(after_phenotype_mib, 1),
        "rssMiBAfter": round(after_mib, 1),
        "rssGrowthMiB": round(after_mib - before_mib, 1),
        "phenotypeCacheVersion": phenotype.get("version"),
        "standardizationCacheVersion": standardization.get("version"),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
