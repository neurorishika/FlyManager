import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

from flymanager.utils.phenotypes.data.balancer_ingest import (
    discover_bdsc_balancers, ingest_balancer_definitions,
    write_balancer_report_files)
from flymanager.utils.phenotypes.data.downloads import (
    REQUIRED_FLYBASE_DOWNLOADS, discover_latest_flybase_downloads,
    download_flybase_bundle)
from flymanager.utils.phenotypes.data.examiner import (
    audit_bloomington_csv, audit_live_stock_standardization,
    audit_stock_csv_standardization,
    examine_flybase_directory, generate_visible_marker_inventory,
    render_bloomington_audit_markdown, render_flybase_examination_markdown,
    render_stock_standardization_markdown,
    render_visible_marker_inventory_markdown, summarize_genotype)
from flymanager.utils.phenotypes.data.flybase_ingest import (
    audit_live_stock_resolution, ingest_flybase_bundle,
    inspect_allele_resolution, render_live_stock_resolution_markdown)

load_dotenv()


def _repo_root():
    return Path(__file__).resolve().parents[4]


def _write_output(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _open_database():
    client = MongoClient(
        os.getenv("MONGO_URI", "mongodb://mongodb:27017").strip(),
        serverSelectionTimeoutMS=int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
        connectTimeoutMS=int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "5000")),
    )
    db = client[os.getenv("MONGO_DB_NAME", "flymanager").strip()]
    try:
        db.command("ping")
    except Exception as exc:
        client.close()
        raise RuntimeError("Could not connect to the configured MongoDB database.") from exc
    return client, db


def build_argument_parser():
    parser = argparse.ArgumentParser(description="Experimental phenotype tooling for FlyManager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser("audit-bloomington", help="Audit current Bloomington genotype packages")
    audit_parser.add_argument(
        "--csv",
        default=str(_repo_root() / "data" / "bloomington.csv"),
        help="Path to bloomington.csv",
    )
    audit_parser.add_argument(
        "--output",
        default=str(_repo_root() / "temp" / "phenotype_parser_report.md"),
        help="Markdown output path",
    )

    standardization_parser = subparsers.add_parser(
        "audit-stock-standardization",
        help="Audit stock CSV genotypes for non-standard phenotype tokens and suggested canonical replacements",
    )
    standardization_parser.add_argument(
        "--csv",
        default=str(_repo_root() / "data" / "bloomington.csv"),
        help="Path to a stock CSV export with a genotype column",
    )
    standardization_parser.add_argument(
        "--genotype-field",
        default="Genotype",
        help="CSV column containing genotype strings",
    )
    standardization_parser.add_argument(
        "--cache-path",
        default=str(_repo_root() / "data" / "flybase" / "PHENOTYPE_EVIDENCE_CACHE.json"),
        help="Path to the cached phenotype evidence JSON used for alias suggestions",
    )
    standardization_parser.add_argument(
        "--output",
        default=str(_repo_root() / "temp" / "stock_standardization_report.md"),
        help="Markdown output path",
    )

    live_standardization_parser = subparsers.add_parser(
        "audit-live-stock-standardization",
        help="Audit live stock genotypes in MongoDB for non-standard phenotype tokens and canonical replacements",
    )
    live_standardization_parser.add_argument(
        "--collection",
        default="stocks",
        help="MongoDB collection name to audit",
    )
    live_standardization_parser.add_argument(
        "--genotype-field",
        default="Genotype",
        help="Field containing genotype strings",
    )
    live_standardization_parser.add_argument(
        "--cache-path",
        default=str(_repo_root() / "data" / "flybase" / "PHENOTYPE_EVIDENCE_CACHE.json"),
        help="Path to the cached phenotype evidence JSON used for alias suggestions",
    )
    live_standardization_parser.add_argument(
        "--output",
        default=str(_repo_root() / "temp" / "live_stock_standardization_report.md"),
        help="Markdown output path",
    )

    examine_parser = subparsers.add_parser("examine", help="Examine FlyBase TSV files in data/flybase")
    examine_parser.add_argument(
        "--data-dir",
        default=str(_repo_root() / "data" / "flybase"),
        help="Directory containing FlyBase TSV files",
    )
    examine_parser.add_argument(
        "--output",
        default=str(_repo_root() / "data" / "flybase" / "EXAMINATION_REPORT.md"),
        help="Markdown output path",
    )

    marker_inventory_parser = subparsers.add_parser(
        "marker-inventory",
        help="Extract unconditional visible FlyBase marker alleles and intersect them with compatible Bloomington stock usage",
    )
    marker_inventory_parser.add_argument(
        "--phenotype-file",
        default=str(_repo_root() / "data" / "flybase" / "genotype_phenotype_data_fb_2026_01.tsv"),
        help="Path to genotype_phenotype_data TSV or TSV.GZ",
    )
    marker_inventory_parser.add_argument(
        "--csv",
        default=str(_repo_root() / "data" / "bloomington.csv"),
        help="Path to bloomington.csv used for compatible stock usage counts",
    )
    marker_inventory_parser.add_argument(
        "--high-priority-threshold",
        type=int,
        default=100,
        help="Compatible stock occurrence threshold that promotes a gene stem into the high-priority curation queue",
    )
    marker_inventory_parser.add_argument(
        "--output",
        default=str(_repo_root() / "temp" / "visible_marker_inventory.md"),
        help="Markdown output path",
    )

    discover_parser = subparsers.add_parser("discover-flybase", help="Discover current FlyBase bulk-data download URLs")
    discover_parser.add_argument(
        "--output",
        help="Optional JSON output path for discovered download URLs",
    )
    discover_parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Network timeout in seconds",
    )

    download_parser = subparsers.add_parser("download-flybase", help="Download latest FlyBase files into data/flybase")
    download_parser.add_argument(
        "--data-dir",
        default=str(_repo_root() / "data" / "flybase"),
        help="Directory where FlyBase files should be stored",
    )
    download_parser.add_argument(
        "--only",
        nargs="*",
        choices=[*REQUIRED_FLYBASE_DOWNLOADS.keys(), "dpo"],
        help="Optional subset of files to download",
    )
    download_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace files already present in the target directory",
    )
    download_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be downloaded without fetching files",
    )
    download_parser.add_argument(
        "--output",
        help="Optional JSON output path for the download manifest",
    )
    download_parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Network timeout in seconds",
    )

    ingest_flybase_parser = subparsers.add_parser(
        "ingest-flybase",
        help="Ingest the local FlyBase TSV bundle into MongoDB collections",
    )
    ingest_flybase_parser.add_argument(
        "--data-dir",
        default=str(_repo_root() / "data" / "flybase"),
        help="Directory containing FlyBase TSV files",
    )
    ingest_flybase_parser.add_argument(
        "--output",
        help="Optional JSON output path for the ingestion report",
    )

    ingest_balancers_parser = subparsers.add_parser(
        "ingest-balancers",
        help="Fetch BDSC balancer reference pages, normalize them, and optionally persist them to MongoDB",
    )
    ingest_balancers_parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Network timeout in seconds",
    )
    ingest_balancers_parser.add_argument(
        "--persist-db",
        action="store_true",
        help="Persist parsed balancer definitions into the balancer_definitions collection",
    )
    ingest_balancers_parser.add_argument(
        "--json-output",
        default=str(_repo_root() / "data" / "flybase" / "BALANCER_DEFINITIONS.json"),
        help="JSON output path",
    )
    ingest_balancers_parser.add_argument(
        "--markdown-output",
        default=str(_repo_root() / "data" / "flybase" / "BALANCER_REPORT.md"),
        help="Markdown output path",
    )

    allele_parser = subparsers.add_parser(
        "test-allele",
        help="Inspect how an allele or bare token resolves across curated, cached, and ingested sources",
    )
    allele_parser.add_argument("token", help="Allele token or bare marker alias")
    allele_parser.add_argument(
        "--with-db",
        action="store_true",
        help="Also query MongoDB ingestion collections if available",
    )

    live_audit_parser = subparsers.add_parser(
        "audit-live-resolution",
        help="Audit current stock genotypes in MongoDB against the phenotype resolver",
    )
    live_audit_parser.add_argument(
        "--collection",
        default="stocks",
        help="Collection name to audit",
    )
    live_audit_parser.add_argument(
        "--output",
        default=str(_repo_root() / "temp" / "live_stock_resolution_report.md"),
        help="Markdown output path",
    )

    genotype_parser = subparsers.add_parser("test-genotype", help="Summarize experimental phenotype markers for a genotype")
    genotype_parser.add_argument("genotype", help="Genotype string in qc_genotype format")
    genotype_parser.add_argument("--sex", default="female", choices=["male", "female"], help="Sex used for phenotype rules")

    return parser


def main(argv=None):
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.command == "audit-bloomington":
        audit = audit_bloomington_csv(args.csv)
        output_path = _write_output(args.output, render_bloomington_audit_markdown(audit))
        print(f"Bloomington audit written to {output_path}")
        return 0

    if args.command == "audit-stock-standardization":
        report = audit_stock_csv_standardization(
            args.csv,
            genotype_field=args.genotype_field,
            cache_path=args.cache_path,
        )
        output_path = _write_output(args.output, render_stock_standardization_markdown(report))
        print(f"Stock standardization audit written to {output_path}")
        return 0

    if args.command == "audit-live-stock-standardization":
        client, db = _open_database()
        try:
            report = audit_live_stock_standardization(
                db,
                collection_name=args.collection,
                genotype_field=args.genotype_field,
                cache_path=args.cache_path,
            )
        finally:
            client.close()
        output_path = _write_output(args.output, render_stock_standardization_markdown(report))
        print(f"Live stock standardization audit written to {output_path}")
        return 0

    if args.command == "examine":
        examination = examine_flybase_directory(args.data_dir)
        output_path = _write_output(args.output, render_flybase_examination_markdown(examination))
        print(f"FlyBase examination report written to {output_path}")
        return 0

    if args.command == "marker-inventory":
        report = generate_visible_marker_inventory(
            args.phenotype_file,
            args.csv,
            high_priority_threshold=args.high_priority_threshold,
        )
        output_path = _write_output(args.output, render_visible_marker_inventory_markdown(report))
        print(f"Visible marker inventory written to {output_path}")
        return 0

    if args.command == "discover-flybase":
        manifest = discover_latest_flybase_downloads(timeout=args.timeout)
        if args.output:
            output_path = _write_output(args.output, json.dumps(manifest, indent=2, sort_keys=True))
            print(f"FlyBase download manifest written to {output_path}")
        else:
            print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    if args.command == "download-flybase":
        report = download_flybase_bundle(
            args.data_dir,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            timeout=args.timeout,
            required_keys=args.only,
        )
        if args.output:
            output_path = _write_output(args.output, json.dumps(report, indent=2, sort_keys=True))
            print(f"FlyBase download report written to {output_path}")
        else:
            print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.command == "ingest-flybase":
        client, db = _open_database()
        try:
            report = ingest_flybase_bundle(args.data_dir, db)
        finally:
            client.close()

        if args.output:
            output_path = _write_output(args.output, json.dumps(report, indent=2, sort_keys=True))
            print(f"FlyBase ingestion report written to {output_path}")
        else:
            print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.command == "ingest-balancers":
        report = discover_bdsc_balancers(timeout=args.timeout)
        file_report = write_balancer_report_files(
            report,
            json_path=args.json_output,
            markdown_path=args.markdown_output,
        )

        db_report = None
        if args.persist_db:
            client, db = _open_database()
            try:
                db_report = ingest_balancer_definitions(db, report)
            finally:
                client.close()

        console_report = {
            "files": file_report,
            "db": db_report,
            "summary": report["definitions"]["summary"],
        }
        print(json.dumps(console_report, indent=2, sort_keys=True))
        return 0

    if args.command == "test-allele":
        client = None
        db = None
        if args.with_db:
            client, db = _open_database()
        try:
            report = inspect_allele_resolution(args.token, db=db)
        finally:
            if client is not None:
                client.close()
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    if args.command == "audit-live-resolution":
        client, db = _open_database()
        try:
            report = audit_live_stock_resolution(db, collection_name=args.collection)
        finally:
            client.close()
        output_path = _write_output(args.output, render_live_stock_resolution_markdown(report))
        print(f"Live stock resolution audit written to {output_path}")
        return 0

    if args.command == "test-genotype":
        summary = summarize_genotype(args.genotype, args.sex)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())