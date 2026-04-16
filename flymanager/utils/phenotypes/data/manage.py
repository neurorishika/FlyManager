import argparse
import json
from pathlib import Path

from flymanager.utils.phenotypes.data.downloads import (
    REQUIRED_FLYBASE_DOWNLOADS, discover_latest_flybase_downloads,
    download_flybase_bundle)
from flymanager.utils.phenotypes.data.examiner import (
    audit_bloomington_csv, examine_flybase_directory,
    render_bloomington_audit_markdown, render_flybase_examination_markdown,
    summarize_genotype)


def _repo_root():
    return Path(__file__).resolve().parents[4]


def _write_output(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


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

    if args.command == "examine":
        examination = examine_flybase_directory(args.data_dir)
        output_path = _write_output(args.output, render_flybase_examination_markdown(examination))
        print(f"FlyBase examination report written to {output_path}")
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

    if args.command == "test-genotype":
        summary = summarize_genotype(args.genotype, args.sex)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())