import json
from datetime import datetime
from pathlib import Path

from flymanager.app.services.bloomington import update_gene_collections
from flymanager.utils.phenotypes.backfill import (
    backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)
from flymanager.utils.phenotypes.data.balancer_ingest import (
    discover_bdsc_balancers, ingest_balancer_definitions,
    write_balancer_report_files)
from flymanager.utils.phenotypes.data.downloads import (
    DEFAULT_TIMEOUT_SECONDS, discover_latest_flybase_downloads,
    download_flybase_bundle)
from flymanager.utils.phenotypes.data.examiner import (
    audit_bloomington_csv, examine_flybase_directory,
    render_flybase_examination_markdown,
    render_unresolved_token_curation_markdown)
from flymanager.utils.phenotypes.data.flybase_ingest import \
    ingest_flybase_bundle
from flymanager.utils.phenotypes.flybase_pipeline import \
    build_flybase_phenotype_cache
from flymanager.utils.stock_sources import (
    collect_compatible_gene_metadata_from_flybase, resolve_flybase_stocks_path)

FLYBASE_SYNC_STALE_DAYS = 45
BALANCER_JSON_FILENAME = "BALANCER_DEFINITIONS.json"
BALANCER_MARKDOWN_FILENAME = "BALANCER_REPORT.md"


def _repo_root(app):
    return Path(app.root_path).parent.parent


def _write_text_file(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json_file(path, payload):
    return _write_text_file(path, json.dumps(payload, indent=2, sort_keys=True))


def _read_json_file(path):
    path = Path(path)
    if not path.exists():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def _path_timestamp(path):
    path = Path(path)
    if not path.exists():
        return ""

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _parse_timestamp(value):
    normalized_value = str(value or "").strip()
    if not normalized_value:
        return None

    normalized_value = normalized_value.replace("T", " ").rstrip("Z")
    for timestamp_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized_value, timestamp_format)
        except ValueError:
            continue

    return None


def _default_reference_status(manifest_path, examination_output_path, curation_output_path):
    return {
        "has_sync": False,
        "release": "",
        "synced_at": "",
        "last_attempt_at": "",
        "last_attempt_status": "",
        "last_error": "",
        "downloaded_count": 0,
        "skipped_count": 0,
        "total_files": 0,
        "supported_rows": None,
        "days_since_sync": None,
        "manifest_path": str(manifest_path),
        "examination_output_path": str(examination_output_path),
        "examination_exists": examination_output_path.exists(),
        "curation_output_path": str(curation_output_path),
        "curation_report_exists": curation_output_path.exists(),
        "unresolved_token_report": {},
        "flybase_ingestion_report": {},
        "flybase_ingestion_collections": {},
        "flybase_ingested_collection_count": 0,
        "flybase_ingested_documents": 0,
        "balancer_ingestion_report": {},
        "balancer_count": 0,
        "balancer_json_path": "",
        "balancer_json_exists": False,
        "balancer_markdown_path": "",
        "balancer_markdown_exists": False,
        "phenotype_cache_report": {},
        "phenotype_cache_source_kind": "",
        "phenotype_cache_source_label": "Unknown",
        "phenotype_cache_summary": {},
        "status_state": "missing",
        "status_label": "Not Synced",
        "status_tone": "warning",
        "status_message": "No successful FlyBase release sync has been recorded yet.",
        "status_detail": "Run the primary FlyBase release sync to create the local manifest and examination report.",
    }


def _summarize_collection_inserts(ingestion_report):
    if not isinstance(ingestion_report, dict):
        return {}

    collections = ingestion_report.get("collections")
    if not isinstance(collections, dict):
        return {}

    summary = {}
    for collection_name, report in collections.items():
        if not isinstance(report, dict):
            continue
        try:
            summary[collection_name] = int(report.get("inserted", 0) or 0)
        except (TypeError, ValueError):
            summary[collection_name] = 0
    return summary


def _cache_source_label(source_kind):
    if source_kind == "mongo_ingest":
        return "Mongo ingest"
    if source_kind == "files":
        return "Raw files"
    return "Unknown"


def _refresh_balancer_reference_data(app, data_dir, db, *, timeout, timestamp):
    del app, timestamp
    balancer_json_path = Path(data_dir) / BALANCER_JSON_FILENAME
    balancer_markdown_path = Path(data_dir) / BALANCER_MARKDOWN_FILENAME
    report = {
        "files": {
            "json_path": str(balancer_json_path),
            "markdown_path": str(balancer_markdown_path),
        }
    }

    discovered_report = discover_bdsc_balancers(timeout=timeout)
    file_report = write_balancer_report_files(
        discovered_report,
        json_path=balancer_json_path,
        markdown_path=balancer_markdown_path,
    )
    db_report = ingest_balancer_definitions(db, discovered_report)
    report.update(
        {
            "definitions": discovered_report.get("definitions", {}),
            "intro": discovered_report.get("intro", {}),
            "files": file_report,
            "db": db_report,
        }
    )
    return report


def _format_day_age(days_since_sync):
    if days_since_sync is None:
        return "unknown age"
    if days_since_sync <= 0:
        return "today"
    if days_since_sync == 1:
        return "1 day ago"
    return f"{days_since_sync} days ago"


def _apply_reference_health(status):
    synced_at = _parse_timestamp(status.get("synced_at"))
    days_since_sync = None
    if synced_at is not None:
        days_since_sync = max((datetime.now() - synced_at).days, 0)

    status["days_since_sync"] = days_since_sync

    last_attempt_at = status.get("last_attempt_at") or status.get("synced_at")
    last_attempt_status = str(status.get("last_attempt_status") or "").strip().lower()
    has_sync = bool(status.get("has_sync"))

    if last_attempt_status == "failed":
        status.update(
            {
                "status_state": "failed",
                "status_label": "Failed",
                "status_tone": "danger",
                "status_message": (
                    f"The last FlyBase refresh attempt failed at {last_attempt_at}."
                    if last_attempt_at
                    else "The last FlyBase refresh attempt failed."
                ),
                "status_detail": (
                    f"Last successful sync: {status['synced_at']} ({_format_day_age(days_since_sync)})."
                    if has_sync and status.get("synced_at")
                    else "No successful FlyBase sync is recorded yet."
                ),
            }
        )
        return status

    if not has_sync:
        status.update(
            {
                "status_state": "missing",
                "status_label": "Not Synced",
                "status_tone": "warning",
                "status_message": "No successful FlyBase release sync has been recorded yet.",
                "status_detail": (
                    f"Last attempted refresh: {last_attempt_at}."
                    if last_attempt_at
                    else "Run the primary FlyBase release sync to establish the local manifest and examination report."
                ),
            }
        )
        return status

    if days_since_sync is not None and days_since_sync > FLYBASE_SYNC_STALE_DAYS:
        status.update(
            {
                "status_state": "stale",
                "status_label": "Stale",
                "status_tone": "warning",
                "status_message": f"The latest successful FlyBase sync is {_format_day_age(days_since_sync)}.",
                "status_detail": "Monthly refresh may have been skipped or delayed. Review the admin sync controls and rerun if needed.",
            }
        )
        return status

    if not status.get("examination_exists"):
        status.update(
            {
                "status_state": "stale",
                "status_label": "Report Missing",
                "status_tone": "warning",
                "status_message": "The last FlyBase sync succeeded, but the examination report is missing.",
                "status_detail": "Run the primary FlyBase release sync again to regenerate the local report files.",
            }
        )
        return status

    status.update(
        {
            "status_state": "healthy",
            "status_label": "Healthy",
            "status_tone": "success",
            "status_message": f"FlyBase reference data is current based on the latest successful sync from {_format_day_age(days_since_sync)}.",
            "status_detail": (
                f"Release {status['release']} synced at {status['synced_at']}."
                if status.get("release") and status.get("synced_at")
                else "The latest successful sync completed without recorded warnings."
            ),
        }
    )
    return status


def _record_flybase_sync_failure(app, manifest_path, examination_output_path, *, timestamp, error_message):
    manifest_payload = {}
    try:
        existing_manifest = _read_json_file(manifest_path)
        if isinstance(existing_manifest, dict):
            manifest_payload.update(existing_manifest)
    except (OSError, json.JSONDecodeError) as exc:
        app.logger.warning(
            "Unable to read existing FlyBase sync manifest %s before recording failure: %s",
            manifest_path,
            exc,
        )

    manifest_payload.update(
        {
            "manifest_path": str(manifest_path),
            "examination_output_path": str(examination_output_path),
            "last_attempt_at": timestamp,
            "last_attempt_status": "failed",
            "last_error": error_message,
        }
    )
    _write_json_file(manifest_path, manifest_payload)


def refresh_flybase_reference_data(
    app,
    data_dir=None,
    manifest_path=None,
    examination_output_path=None,
    curation_output_path=None,
    overwrite=True,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    renew_phenotype_caches=True,
    bloomington_csv_path=None,
):
    with app.app_context():
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_dir = Path(data_dir) if data_dir is not None else _repo_root(app) / "data" / "flybase"
        manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else data_dir / "LATEST_DOWNLOAD_MANIFEST.json"
        )
        examination_output_path = (
            Path(examination_output_path)
            if examination_output_path is not None
            else data_dir / "EXAMINATION_REPORT.md"
        )
        curation_output_path = (
            Path(curation_output_path)
            if curation_output_path is not None
            else data_dir / "UNRESOLVED_TOKEN_REPORT.md"
        )
        bloomington_csv_path = (
            Path(bloomington_csv_path)
            if bloomington_csv_path is not None
            else _repo_root(app) / "data" / "bloomington.csv"
        )

        try:
            from flymanager.app import db

            app.logger.info("[%s] Starting FlyBase reference data refresh into %s", timestamp, data_dir)
            bundle = discover_latest_flybase_downloads(timeout=timeout)
            app.logger.info("[%s] Discovered latest FlyBase release %s", timestamp, bundle["release"])

            download_report = download_flybase_bundle(
                data_dir,
                overwrite=overwrite,
                timeout=timeout,
                bundle=bundle,
            )

            examination = examine_flybase_directory(data_dir)
            _write_text_file(
                examination_output_path,
                render_flybase_examination_markdown(examination),
            )

            unresolved_token_report = {
                "csv_path": str(bloomington_csv_path),
                "error": "Bloomington audit not generated.",
            }
            try:
                unresolved_token_report = audit_bloomington_csv(
                    bloomington_csv_path,
                    data_dir=data_dir,
                )
                _write_text_file(
                    curation_output_path,
                    render_unresolved_token_curation_markdown(unresolved_token_report),
                )
            except Exception as exc:
                app.logger.exception(
                    "[%s] FlyBase unresolved-token curation report generation failed",
                    timestamp,
                )
                unresolved_token_report = {
                    "csv_path": str(bloomington_csv_path),
                    "error": str(exc),
                }

            stocks_download = bundle["downloads"].get("stocks")
            if stocks_download is None:
                raise ValueError("The latest FlyBase bundle did not include a stocks dataset")

            stocks_file_path = data_dir / stocks_download["filename"]
            gene_summary = update_gene_metadata_from_flybase(
                app,
                stocks_file_path=stocks_file_path,
                timestamp=timestamp,
            )
            balancer_ingestion_report = {
                "files": {
                    "json_path": str(data_dir / BALANCER_JSON_FILENAME),
                    "markdown_path": str(data_dir / BALANCER_MARKDOWN_FILENAME),
                }
            }
            try:
                balancer_ingestion_report = _refresh_balancer_reference_data(
                    app,
                    data_dir,
                    db,
                    timeout=timeout,
                    timestamp=timestamp,
                )
            except Exception as exc:
                app.logger.exception(
                    "[%s] Balancer reference refresh failed",
                    timestamp,
                )
                balancer_ingestion_report["error"] = str(exc)

            flybase_ingestion_report = ingest_flybase_bundle(data_dir, db)
            phenotype_cache_report = build_flybase_phenotype_cache(
                data_dir=data_dir,
                force=True,
                db=db,
            )
            phenotype_backfill_summary = {
                "stocks": None,
                "crosses": None,
            }
            if renew_phenotype_caches:
                try:
                    phenotype_backfill_summary = {
                        "stocks": backfill_stock_phenotype_cache(
                            db["stocks"],
                            dry_run=False,
                            force=True,
                        ),
                        "crosses": backfill_cross_phenotype_cache(
                            db["crosses"],
                            dry_run=False,
                            force=True,
                        ),
                    }
                except Exception as exc:
                    app.logger.exception(
                        "[%s] FlyBase phenotype cache renewal failed during refresh",
                        timestamp,
                    )
                    phenotype_backfill_summary = {
                        "stocks": None,
                        "crosses": None,
                        "error": str(exc),
                    }

            manifest_payload = {
                **download_report,
                "synced_at": timestamp,
                "last_attempt_at": timestamp,
                "last_attempt_status": "success",
                "last_error": "",
                "manifest_path": str(manifest_path),
                "examination_output_path": str(examination_output_path),
                "curation_output_path": str(curation_output_path),
                "gene_summary": gene_summary,
                "balancer_ingestion_report": balancer_ingestion_report,
                "flybase_ingestion_report": flybase_ingestion_report,
                "phenotype_cache_report": phenotype_cache_report,
                "phenotype_backfill_summary": phenotype_backfill_summary,
                "unresolved_token_report": unresolved_token_report,
            }
            _write_json_file(manifest_path, manifest_payload)

            app.logger.info(
                "[%s] FlyBase reference data refresh complete for release %s",
                timestamp,
                download_report["release"],
            )

            return {
                "release": download_report["release"],
                "data_dir": str(data_dir),
                "manifest_path": str(manifest_path),
                "examination_output_path": str(examination_output_path),
                "curation_output_path": str(curation_output_path),
                "download_report": manifest_payload,
                "gene_summary": gene_summary,
                "balancer_ingestion_report": balancer_ingestion_report,
                "flybase_ingestion_report": flybase_ingestion_report,
                "phenotype_cache_report": phenotype_cache_report,
                "phenotype_backfill_summary": phenotype_backfill_summary,
                "unresolved_token_report": unresolved_token_report,
            }
        except Exception as exc:
            _record_flybase_sync_failure(
                app,
                manifest_path,
                examination_output_path,
                timestamp=timestamp,
                error_message=str(exc),
            )
            raise


def get_flybase_reference_status(
    app,
    data_dir=None,
    manifest_path=None,
    examination_output_path=None,
    curation_output_path=None,
):
    with app.app_context():
        data_dir = Path(data_dir) if data_dir is not None else _repo_root(app) / "data" / "flybase"
        manifest_path = (
            Path(manifest_path)
            if manifest_path is not None
            else data_dir / "LATEST_DOWNLOAD_MANIFEST.json"
        )
        examination_output_path = (
            Path(examination_output_path)
            if examination_output_path is not None
            else data_dir / "EXAMINATION_REPORT.md"
        )
        curation_output_path = (
            Path(curation_output_path)
            if curation_output_path is not None
            else data_dir / "UNRESOLVED_TOKEN_REPORT.md"
        )

        status = _default_reference_status(
            manifest_path,
            examination_output_path,
            curation_output_path,
        )

        try:
            manifest = _read_json_file(manifest_path)
        except (OSError, json.JSONDecodeError) as exc:
            app.logger.warning("Unable to read FlyBase sync manifest %s: %s", manifest_path, exc)
            status.update(
                {
                    "last_attempt_status": "failed",
                    "last_attempt_at": _path_timestamp(manifest_path),
                    "last_error": f"Unable to read manifest: {exc}",
                }
            )
            return _apply_reference_health(status)

        if not isinstance(manifest, dict):
            return _apply_reference_health(status)

        results = manifest.get("results")
        if not isinstance(results, list):
            results = []

        gene_summary = manifest.get("gene_summary")
        if not isinstance(gene_summary, dict):
            gene_summary = {}

        unresolved_token_report = manifest.get("unresolved_token_report")
        if not isinstance(unresolved_token_report, dict):
            unresolved_token_report = {}

        balancer_ingestion_report = manifest.get("balancer_ingestion_report")
        if not isinstance(balancer_ingestion_report, dict):
            balancer_ingestion_report = {}

        flybase_ingestion_report = manifest.get("flybase_ingestion_report")
        if not isinstance(flybase_ingestion_report, dict):
            flybase_ingestion_report = {}

        phenotype_cache_report = manifest.get("phenotype_cache_report")
        if not isinstance(phenotype_cache_report, dict):
            phenotype_cache_report = {}

        resolved_curation_output_path = Path(
            manifest.get("curation_output_path") or curation_output_path
        )
        balancer_files = balancer_ingestion_report.get("files")
        if not isinstance(balancer_files, dict):
            balancer_files = {}

        flybase_ingestion_collections = _summarize_collection_inserts(flybase_ingestion_report)
        balancer_count = 0
        try:
            balancer_count = int(
                (
                    balancer_ingestion_report.get("db") or {}
                ).get("inserted", 0)
                or (
                    (
                        (balancer_ingestion_report.get("definitions") or {}).get("summary")
                        or {}
                    ).get("total_balancers", 0)
                )
                or 0
            )
        except (TypeError, ValueError):
            balancer_count = 0

        phenotype_cache_source_kind = str(
            phenotype_cache_report.get("source_kind") or ""
        )

        status.update(
            {
                "has_sync": bool(manifest.get("synced_at")),
                "release": str(manifest.get("release") or ""),
                "synced_at": str(manifest.get("synced_at") or _path_timestamp(manifest_path)),
                "last_attempt_at": str(manifest.get("last_attempt_at") or manifest.get("synced_at") or ""),
                "last_attempt_status": str(manifest.get("last_attempt_status") or ""),
                "last_error": str(manifest.get("last_error") or ""),
                "downloaded_count": sum(
                    1 for result in results if result.get("status") == "downloaded"
                ),
                "skipped_count": sum(
                    1 for result in results if result.get("status") == "skipped"
                ),
                "total_files": len(results),
                "supported_rows": gene_summary.get("supported_rows"),
                "curation_output_path": str(resolved_curation_output_path),
                "curation_report_exists": resolved_curation_output_path.exists(),
                "unresolved_token_report": unresolved_token_report,
                "balancer_ingestion_report": balancer_ingestion_report,
                "balancer_count": balancer_count,
                "balancer_json_path": str(balancer_files.get("json_path") or ""),
                "balancer_json_exists": Path(balancer_files.get("json_path", "")).exists() if balancer_files.get("json_path") else False,
                "balancer_markdown_path": str(balancer_files.get("markdown_path") or ""),
                "balancer_markdown_exists": Path(balancer_files.get("markdown_path", "")).exists() if balancer_files.get("markdown_path") else False,
                "flybase_ingestion_report": flybase_ingestion_report,
                "flybase_ingestion_collections": flybase_ingestion_collections,
                "flybase_ingested_collection_count": len(flybase_ingestion_collections),
                "flybase_ingested_documents": sum(flybase_ingestion_collections.values()),
                "phenotype_cache_report": phenotype_cache_report,
                "phenotype_cache_source_kind": phenotype_cache_source_kind,
                "phenotype_cache_source_label": _cache_source_label(phenotype_cache_source_kind),
                "phenotype_cache_summary": phenotype_cache_report.get("summary") or {},
            }
        )
        return _apply_reference_health(status)


def update_flybase_reference_data(app):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    app.logger.info("[%s] Starting scheduled FlyBase reference refresh", timestamp)

    try:
        report = refresh_flybase_reference_data(app)
        app.logger.info(
            "[%s] Scheduled FlyBase reference refresh completed for release %s",
            timestamp,
            report["release"],
        )
        return report
    except Exception:
        app.logger.exception("[%s] Scheduled FlyBase reference refresh failed", timestamp)
        return None


def update_gene_metadata_from_flybase(app, stocks_file_path=None, timestamp=None):
    with app.app_context():
        from flymanager.app import db

        timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resolved_stocks_path = resolve_flybase_stocks_path(stocks_file_path)
        print(f"[{timestamp}] Starting FlyBase gene metadata update from {resolved_stocks_path}...")

        all_components, summary = collect_compatible_gene_metadata_from_flybase(
            resolved_stocks_path
        )

        print(f"[{timestamp}] FlyBase rows scanned: {summary['total_rows']}")
        print(f"[{timestamp}] Dmel rows scanned: {summary['dmel_rows']}")
        print(f"[{timestamp}] Compatible rows harvested: {summary['supported_rows']}")
        print(f"[{timestamp}] Unsupported rows skipped: {summary['unsupported_rows']}")

        for chromosome_index, components in all_components.items():
            print(
                f"[{timestamp}] Compatible components for chromosome {chromosome_index + 1}: {len(components)}"
            )

        update_gene_collections(all_components, db, timestamp)
        print(f"[{timestamp}] FlyBase gene metadata update completed successfully")
        return summary


def manual_update_flybase_gene_metadata_only(app, stocks_file_path=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return update_gene_metadata_from_flybase(
        app,
        stocks_file_path=stocks_file_path,
        timestamp=timestamp,
    )


def manual_refresh_flybase_reference_data(app, overwrite=True, timeout=DEFAULT_TIMEOUT_SECONDS):
    return refresh_flybase_reference_data(
        app,
        overwrite=overwrite,
        timeout=timeout,
    )