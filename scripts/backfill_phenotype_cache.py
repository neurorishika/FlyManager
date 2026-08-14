#!/usr/bin/env python

import argparse
import os

from dotenv import load_dotenv
from pymongo import MongoClient

from flymanager.app.services.standardization_backfill import (
    backfill_cross_standardization_cache, backfill_stock_standardization_cache)
from flymanager.utils.phenotypes.backfill import (
    backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)

load_dotenv()


def get_mongo_uri():
    return os.getenv("MONGO_URI", "mongodb://mongodb:27017").strip()


def get_mongo_db_name():
    return os.getenv("MONGO_DB_NAME", "flymanager").strip()


def create_mongo_client():
    return MongoClient(
        get_mongo_uri(),
        serverSelectionTimeoutMS=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
        connectTimeoutMS=int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    )


def get_database(client):
    return client[get_mongo_db_name()]


def ping_database(db):
    try:
        db.command("ping")
        return True
    except Exception:
        return False


def _build_argument_parser():
    parser = argparse.ArgumentParser(
        description="Backfill cached phenotype payloads for FlyManager stocks and crosses.",
    )
    parser.add_argument(
        "--stocks",
        action="store_true",
        help="Backfill stock phenotype caches.",
    )
    parser.add_argument(
        "--crosses",
        action="store_true",
        help="Backfill cross phenotype caches.",
    )
    parser.add_argument(
        "--user",
        dest="users",
        action="append",
        default=[],
        help="Limit the backfill to a specific owner username. Repeat to include multiple users.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many records would be updated without writing any changes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild caches even when a valid cache is already present.",
    )
    parser.add_argument(
        "--phenotype",
        action="store_true",
        help="Backfill phenotype caches. Defaults on when no cache type is selected.",
    )
    parser.add_argument(
        "--standardization",
        action="store_true",
        help="Backfill standardization caches. Defaults on when no cache type is selected.",
    )
    return parser


def _print_summary(label, summary, *, dry_run):
    mode_label = "would update" if dry_run else "updated"
    print(
        f"{label}: scanned={summary['scanned']} {mode_label}={summary['updated']} skipped_valid={summary['skipped_valid']}"
    )


def main():
    parser = _build_argument_parser()
    args = parser.parse_args()

    run_stocks = args.stocks or not (args.stocks or args.crosses)
    run_crosses = args.crosses or not (args.stocks or args.crosses)
    run_phenotype = args.phenotype or not (args.phenotype or args.standardization)
    run_standardization = args.standardization or not (args.phenotype or args.standardization)

    client = create_mongo_client()
    try:
        db = get_database(client)
        if not ping_database(db):
            parser.error("Could not connect to the configured MongoDB database.")

        cache_backfills = []
        if run_phenotype:
            cache_backfills.append(
                ("phenotype", backfill_stock_phenotype_cache, backfill_cross_phenotype_cache)
            )
        if run_standardization:
            cache_backfills.append(
                ("standardization", backfill_stock_standardization_cache, backfill_cross_standardization_cache)
            )

        for cache_label, stock_backfill, cross_backfill in cache_backfills:
            if run_stocks:
                stock_summary = stock_backfill(
                    db["stocks"],
                    users=args.users,
                    dry_run=args.dry_run,
                    force=args.force,
                )
                _print_summary(f"stocks/{cache_label}", stock_summary, dry_run=args.dry_run)

            if run_crosses:
                cross_summary = cross_backfill(
                    db["crosses"],
                    users=args.users,
                    dry_run=args.dry_run,
                    force=args.force,
                )
                _print_summary(f"crosses/{cache_label}", cross_summary, dry_run=args.dry_run)

    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())