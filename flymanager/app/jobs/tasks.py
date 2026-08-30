"""Background task functions run by the RQ worker (flymanager.app.worker).

Each of these used to be the body of a synchronous, request-blocking admin
route. They're plain top-level functions (importable by the worker process)
so RQ can enqueue them by reference. Every task follows the same shape:
mark the job running, do the work, mark it succeeded/failed - the actual
business logic is unchanged from what used to live inline in the route.
"""
import logging
import os
from datetime import datetime

from flymanager.app.jobs import get_worker_app
from flymanager.utils.mongo import (mark_job_failed, mark_job_running,
                                    mark_job_succeeded, update_job_progress,
                                    write_activity)

EXPORT_SUBDIR = os.path.join("data", "job_exports")


def _run(key, work):
    """Shared mark-running/succeeded/failed envelope for every task below."""
    app = get_worker_app()
    with app.app_context():
        from flymanager.app import db
        from flymanager.utils.phenotypes.image_catalog import refresh_image_catalog
        from flymanager.utils.phenotypes.marker_catalog import refresh_catalog

        # The worker has no before_request hook, so its marker catalog would
        # otherwise stay at whatever create_app compiled -- shipped-only, with
        # no overlay. Any task that writes a materialized cache must not run
        # against a stale marker set.
        try:
            refresh_catalog(db)
            refresh_image_catalog(db)
        except Exception:
            app.logger.exception("Unable to refresh the marker catalog before job %s", key)

        mark_job_running(db, key)
        try:
            result = work(app, db)
        except Exception as exc:
            app.logger.exception("Background job %s failed", key)
            mark_job_failed(db, key, error=str(exc))
            raise
        else:
            mark_job_succeeded(db, key, result=result)


def _throttled_progress(db, key, *, every=25):
    """Return a (current, total) callback that reports progress sparingly.

    The batched bulk operations iterate in memory and only touch the database
    in a couple of bulk writes, so writing a progress document on every item
    would reintroduce the per-item round-trips we just removed. Report on the
    first item, every ``every`` items, and the final item instead.
    """
    def callback(current, total):
        if current == 1 or current == total or current % every == 0:
            update_job_progress(
                db,
                key,
                current=current,
                total=total,
                message=f"Processed {current} of {total}.",
            )

    return callback


def _summarize(results):
    return len(results.get("success", [])), len(results.get("failed", []))


def task_bulk_flip(key, username, uids, flip_time, status=None, comment=""):
    def work(app, db):
        from flymanager.utils.mongo.bulk_operations import bulk_flip_records

        results = bulk_flip_records(
            username,
            uids,
            db,
            flip_time,
            new_status=status,
            comment=comment,
            progress_cb=_throttled_progress(db, key),
        )
        succeeded, failed = _summarize(results)
        return {
            "message": f"Bulk flip completed. {succeeded} successful, {failed} failed.",
            "results": results,
        }

    _run(key, work)


def task_bulk_status_change(key, username, uids, status, comment=None):
    def work(app, db):
        from flymanager.utils.mongo.bulk_operations import bulk_change_status_records

        results = bulk_change_status_records(
            username,
            uids,
            db,
            status,
            comment=comment,
            progress_cb=_throttled_progress(db, key),
        )
        succeeded, failed = _summarize(results)
        return {
            "message": (
                f"Bulk status change to {status} completed. "
                f"{succeeded} successful, {failed} failed."
            ),
            "results": results,
        }

    _run(key, work)


def task_bulk_remove_from_tray(key, username, uids, item_types):
    def work(app, db):
        from flymanager.utils.mongo.bulk_operations import bulk_remove_from_tray_records

        results = bulk_remove_from_tray_records(
            username,
            uids,
            item_types,
            db,
            progress_cb=_throttled_progress(db, key),
        )
        succeeded, failed = _summarize(results)
        if succeeded:
            write_activity(
                username,
                f"Bulk removed {succeeded} items from trays",
                db,
            )
        return {
            "message": (
                f"Removed {succeeded} of {len(uids)} items from trays."
            ),
            "results": results,
        }

    _run(key, work)


def task_export_excel(key, username):
    def work(app, db):
        from flymanager.utils.converter import mongo_to_xls

        os.makedirs(EXPORT_SUBDIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"fly_manager_data_{username}_{timestamp}.xlsx"
        file_path = os.path.join(EXPORT_SUBDIR, filename)

        mongo_to_xls(db, file_path)
        write_activity(username, "Downloaded data to Excel", db)
        return {
            "message": "Your Excel export is ready to download.",
            "download_filename": filename,
        }

    _run(key, work)


def task_backfill_phenotype_cache(key, username, *, users, scope_label):
    def work(app, db):
        from flymanager.app.services.standardization_backfill import (
            backfill_cross_standardization_cache,
            backfill_stock_standardization_cache)
        from flymanager.utils.phenotypes.backfill import (
            backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)

        stock_summary = backfill_stock_phenotype_cache(
            db["stocks"], users=users, dry_run=False, force=False,
        )
        cross_summary = backfill_cross_phenotype_cache(
            db["crosses"], users=users, dry_run=False, force=False,
        )
        # Keep the materialized standardization summaries in step with the
        # phenotype caches so the reviewer page never has to recompute on read.
        backfill_stock_standardization_cache(
            db["stocks"], users=users, dry_run=False, force=False,
        )
        backfill_cross_standardization_cache(
            db["crosses"], users=users, dry_run=False, force=False,
        )
        totals = {
            "scanned": stock_summary["scanned"] + cross_summary["scanned"],
            "updated": stock_summary["updated"] + cross_summary["updated"],
            "skipped_valid": stock_summary["skipped_valid"] + cross_summary["skipped_valid"],
        }
        message = (
            f"Phenotype cache backfill complete for {scope_label}: "
            f"{totals['updated']} updated, {totals['skipped_valid']} already current, "
            f"{totals['scanned']} scanned total "
            f"(stocks: {stock_summary['updated']} updated / {stock_summary['scanned']} scanned; "
            f"crosses: {cross_summary['updated']} updated / {cross_summary['scanned']} scanned)."
        )
        activity_label = (
            "Backfilled phenotype cache for maintained records"
            if users
            else "Backfilled phenotype cache for all users"
        )
        write_activity(username, activity_label, db)
        return {"message": message, "totals": totals}

    _run(key, work)


def task_rebuild_marker_caches(key, username, *, keys, previous_documents=(),
                               deleted_override_keys=()):
    def work(app, db):
        from flymanager.utils.phenotypes.marker_catalog import refresh_catalog
        from flymanager.utils.phenotypes.marker_rebuild import \
            rebuild_after_marker_change

        # force=True rather than waiting out the before_request interval: the
        # worker must see the edit that triggered this job.
        refresh_catalog(db, force=True)
        summary = rebuild_after_marker_change(
            db, keys, previous_documents=previous_documents,
            deleted_override_keys=deleted_override_keys)
        write_activity(username, f"Rebuilt caches after marker change: {', '.join(keys) or 'catalog'}", db)
        return {
            "message": (
                f"Marker cache rebuild ({summary['scope']}) complete: "
                f"{summary['stocks']['rebuilt']} stocks and "
                f"{summary['crosses']['rebuilt']} crosses recomputed, "
                f"{summary['stocks']['stamped'] + summary['crosses']['stamped']} stamped."
            ),
            "summary": summary,
        }

    _run(key, work)


def task_update_bloomington_stock(key, username):
    def work(app, db):
        from flymanager.app.services import bloomington as bloomington_service

        bloomington_service.manual_update_bloomington_stock(app)
        return {"message": "Legacy Bloomington compatibility refresh completed successfully."}

    _run(key, work)


def task_update_gene_metadata(key, username):
    def work(app, db):
        from flymanager.app.services import bloomington as bloomington_service

        bloomington_service.manual_update_gene_metadata_only(app)
        return {"message": "Legacy Bloomington gene metadata update completed successfully."}

    _run(key, work)


def task_update_flybase_gene_metadata(key, username):
    def work(app, db):
        from flymanager.app.services import flybase as flybase_service

        flybase_service.manual_update_flybase_gene_metadata_only(app)
        return {"message": "FlyBase gene metadata update completed successfully."}

    _run(key, work)


def _rebuild_caches_after_flybase_refresh(db, app=None):
    """Run all five materialized-cache backfills at force=False after a FlyBase
    reference-data refresh. Each wrapper is isolated in its own try/except so
    one failing doesn't block the others; force=False means the cost is
    proportional to how much reference data actually changed, since a valid
    cache entry (unchanged pipelineSignature) is skipped, not rebuilt."""
    from flymanager.app.routes.stock import backfill_stock_provider_match_cache
    from flymanager.app.services.standardization_backfill import (
        backfill_cross_standardization_cache, backfill_stock_standardization_cache)
    from flymanager.utils.phenotypes.backfill import (
        backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)

    wrappers = {
        "stock_phenotype": lambda: backfill_stock_phenotype_cache(
            db["stocks"], users=None, dry_run=False, force=False),
        "cross_phenotype": lambda: backfill_cross_phenotype_cache(
            db["crosses"], users=None, dry_run=False, force=False),
        "stock_standardization": lambda: backfill_stock_standardization_cache(
            db["stocks"], users=None, dry_run=False, force=False),
        "cross_standardization": lambda: backfill_cross_standardization_cache(
            db["crosses"], users=None, dry_run=False, force=False),
        "stock_provider_match": lambda: backfill_stock_provider_match_cache(
            db["stocks"], users=None, dry_run=False, force=False),
    }

    results = {}
    for label, run in wrappers.items():
        try:
            results[label] = run()
        except Exception:
            if app is not None:
                app.logger.exception(
                    "Cache rebuild wrapper %s failed after FlyBase refresh", label
                )
            else:
                logging.getLogger(__name__).exception(
                    "Cache rebuild wrapper %s failed after FlyBase refresh", label
                )
            results[label] = {"error": True}
    return results


def _force_recompute_cache(cache_key, db):
    """Runs the relevant backfill wrapper(s) for cache_key at force=True.

    Unlike _rebuild_caches_after_flybase_refresh, this is a targeted dispatch
    for exactly one cache (the one an admin explicitly chose to force), not a
    fan-out across all five wrappers.
    """
    if cache_key == "phenotype":
        from flymanager.utils.phenotypes.backfill import (
            backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)
        return {
            "stocks": backfill_stock_phenotype_cache(db["stocks"], users=None, dry_run=False, force=True),
            "crosses": backfill_cross_phenotype_cache(db["crosses"], users=None, dry_run=False, force=True),
        }
    if cache_key == "standardization":
        from flymanager.app.services.standardization_backfill import (
            backfill_cross_standardization_cache, backfill_stock_standardization_cache)
        return {
            "stocks": backfill_stock_standardization_cache(db["stocks"], users=None, dry_run=False, force=True),
            "crosses": backfill_cross_standardization_cache(db["crosses"], users=None, dry_run=False, force=True),
        }
    if cache_key == "provider_match":
        from flymanager.app.routes.stock import backfill_stock_provider_match_cache
        return {
            "stocks": backfill_stock_provider_match_cache(db["stocks"], users=None, dry_run=False, force=True),
        }
    raise ValueError(f"Unknown cache_key: {cache_key}")


def task_force_recompute_cache(key, username, cache_key):
    def work(app, db):
        from flymanager.utils.mongo.cache_force_refresh import record_force_refresh

        summary = _force_recompute_cache(cache_key, db)
        record_force_refresh(cache_key, username, db)
        write_activity(
            username,
            f"[FORCE-RECOMPUTE] Forced full recompute of {cache_key} cache",
            db,
        )
        message = f"Forced full recompute of the {cache_key} cache complete: {summary}"
        return {"message": message, "summary": summary}

    _run(key, work)


def task_refresh_flybase_reference_data(key, username):
    def work(app, db):
        from flymanager.app.services import flybase as flybase_service

        report = flybase_service.manual_refresh_flybase_reference_data(app)
        downloaded_count = sum(
            1 for item in report["download_report"]["results"] if item.get("status") == "downloaded"
        )
        skipped_count = sum(
            1 for item in report["download_report"]["results"] if item.get("status") == "skipped"
        )

        cache_rebuild = _rebuild_caches_after_flybase_refresh(db, app=app)
        rebuild_summary = ", ".join(
            f"{label} failed" if "error" in result else f"{label}: {result.get('updated', 0)} updated"
            for label, result in cache_rebuild.items()
        )

        message = (
            f"FlyBase release {report['release']} refresh completed: "
            f"{downloaded_count} files downloaded, {skipped_count} reused, gene metadata refreshed. "
            f"Cache rebuild — {rebuild_summary}."
        )
        return {"message": message, "release": report["release"], "cacheRebuild": cache_rebuild}

    _run(key, work)
