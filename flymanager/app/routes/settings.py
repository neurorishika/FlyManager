from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from flymanager.app import db
from flymanager.app.routes.auth import admin_required, login_required
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_optional_text)
from flymanager.app.services import bloomington as bloomington_service
from flymanager.app.services import flybase as flybase_service
from flymanager.utils.mongo import (OperationLockConflict, get_settings,
                                    get_user_profiles, hold_operation_lock,
                                    update_settings,
                                    update_user_reporting_manager,
                                    write_activity)
from flymanager.utils.phenotypes.backfill import (
    backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)

bp = Blueprint("settings", __name__)


def _run_phenotype_cache_backfill(*, users=None):
    stock_summary = backfill_stock_phenotype_cache(
        db["stocks"],
        users=users,
        dry_run=False,
        force=False,
    )
    cross_summary = backfill_cross_phenotype_cache(
        db["crosses"],
        users=users,
        dry_run=False,
        force=False,
    )
    return {
        "stocks": stock_summary,
        "crosses": cross_summary,
        "totals": {
            "scanned": stock_summary["scanned"] + cross_summary["scanned"],
            "updated": stock_summary["updated"] + cross_summary["updated"],
            "skipped_valid": stock_summary["skipped_valid"] + cross_summary["skipped_valid"],
        },
    }


def _build_phenotype_cache_backfill_message(report, *, scope_label):
    totals = report["totals"]
    return (
        f"Phenotype cache backfill complete for {scope_label}: "
        f"{totals['updated']} updated, {totals['skipped_valid']} already current, {totals['scanned']} scanned total "
        f"(stocks: {report['stocks']['updated']} updated / {report['stocks']['scanned']} scanned; "
        f"crosses: {report['crosses']['updated']} updated / {report['crosses']['scanned']} scanned)."
    )


def _run_provider_match_cache_refresh():
    from flymanager.app.routes.stock import _get_provider_match_payload

    summary = {
        "scanned": 0,
        "refreshed": 0,
        "errors": 0,
        "candidate_matches": 0,
    }
    projection = {
        "UniqueID": 1,
        "User": 1,
        "SourceID": 1,
        "StockSource": 1,
        "SourceCollection": 1,
        "FlyBaseStockID": 1,
        "Genotype": 1,
        "ExternalRawGenotype": 1,
        "AltReference": 1,
        "Provenance": 1,
    }

    for stock in db["stocks"].find({}, projection):
        if not stock.get("UniqueID") or not stock.get("User"):
            continue

        summary["scanned"] += 1
        try:
            payload = _get_provider_match_payload(stock, refresh=True)
        except Exception:
            summary["errors"] += 1
            current_app.logger.exception(
                "Error refreshing provider cache for stock %s",
                stock.get("UniqueID"),
            )
            continue

        summary["refreshed"] += 1
        summary["candidate_matches"] += int(payload.get("count") or 0)

    return summary


def _build_provider_match_cache_refresh_message(report):
    return (
        "Provider cache refresh complete for all stocks: "
        f"{report['refreshed']} refreshed, "
        f"{report['candidate_matches']} candidate matches cached, "
        f"{report['errors']} errors, "
        f"{report['scanned']} scanned total."
    )


def _handle_locked_form_submission(conflict_error, *, redirect_to):
    flash(str(conflict_error), "warning")
    return redirect(redirect_to)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
@limiter.limit("20 per hour")
def admin_settings():
    if request.method == "POST":
        if request.is_json:
            # Handle AJAX requests for theme toggle
            try:
                payload = get_json_payload()
            except ValueError as exc:
                return jsonify({"status": "error", "message": str(exc)}), 400

            theme = payload.get("theme", {})
            if not isinstance(theme, dict):
                return jsonify({"status": "error", "message": "Theme payload must be an object."}), 400

            accent_color = normalize_optional_text(
                theme.get("accent_color"), field_name="Accent color", max_length=32
            )
            updates = {
                "theme": {
                    "dark_mode": bool(theme.get("dark_mode")),
                    "accent_color": accent_color,
                }
            }
            if update_settings(updates, db):
                return jsonify({"status": "success"})
            return jsonify({"status": "error"}), 500

        # Handle form submissions
        updates = {
            "theme": {
                "dark_mode": bool(request.form.get("dark_mode")),
                "accent_color": normalize_optional_text(
                    request.form.get("accent_color"), field_name="Accent color", max_length=32
                ),
            },
            "lab_info": {
                "lab_name": normalize_optional_text(
                    request.form.get("lab_name"), field_name="Lab name", max_length=128
                ),
                "admin_name": normalize_optional_text(
                    request.form.get("admin_name"), field_name="Admin name", max_length=128
                ),
                "admin_email": normalize_optional_text(
                    request.form.get("admin_email"), field_name="Admin email", max_length=255
                ),
            },
        }

        if update_settings(updates, db):
            write_activity("admin", "Updated application settings", db)
            flash("Settings updated successfully.", "success")
        else:
            flash("Failed to update settings.", "error")

        return redirect(url_for("settings.admin_settings"))

    settings = get_settings(db)
    user_profiles = get_user_profiles(db)
    manager_options = [profile["Username"] for profile in user_profiles]
    flybase_reference_status = flybase_service.get_flybase_reference_status(
        current_app._get_current_object()
    )
    return render_template(
        "settings/admin.html",
        settings=settings,
        user_profiles=user_profiles,
        manager_options=manager_options,
        flybase_reference_status=flybase_reference_status,
    )


@bp.route("/settings/user-hierarchy", methods=["POST"])
@login_required
@admin_required
@limiter.limit("20 per hour")
def update_user_hierarchy():
    try:
        user_profiles = get_user_profiles(db)
        for profile in user_profiles:
            username = profile.get("Username")
            if not username or username == "admin":
                continue

            manager_username = normalize_optional_text(
                request.form.get(f"manager__{username}"),
                field_name=f"Manager for {username}",
                max_length=32,
            )
            success, error_message = update_user_reporting_manager(
                username,
                manager_username,
                db,
            )
            if not success:
                flash(error_message or f"Unable to update reporting line for {username}.", "error")
                return redirect(url_for("settings.admin_settings"))

        write_activity("admin", "Updated user reporting hierarchy", db)
        flash("User reporting hierarchy updated successfully.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception as exc:
        current_app.logger.exception("Error updating user hierarchy: %s", exc)
        flash("An internal error occurred while updating the user hierarchy.", "error")

    return redirect(url_for("settings.admin_settings"))


@bp.route("/settings/backfill-my-phenotype-cache", methods=["POST"])
@login_required
@limiter.limit("4 per hour")
def backfill_my_phenotype_cache():
    username = session.get("username")
    redirect_to = request.referrer or url_for("main.home")

    try:
        with hold_operation_lock(
            db,
            key=f"maintenance:phenotype-backfill:user:{username}",
            actor=username,
            label="Phenotype cache backfill",
            ttl_seconds=1800,
            metadata={"route": "backfill_my_phenotype_cache", "scope": "owned_records"},
            conflict_message="Your phenotype cache backfill is already running. Please wait for it to finish before starting it again.",
        ):
            report = _run_phenotype_cache_backfill(users=[username])
            write_activity(username, "Backfilled phenotype cache for maintained records", db)
        flash(
            _build_phenotype_cache_backfill_message(report, scope_label="your maintained records"),
            "success",
        )
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=redirect_to)
    except Exception as exc:
        current_app.logger.exception("Error backfilling phenotype cache for %s: %s", username, exc)
        flash(f"Error backfilling phenotype caches for your records: {str(exc)}", "error")

    return redirect(redirect_to)


@bp.route("/settings/backfill-all-phenotype-cache", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def backfill_all_phenotype_cache():
    username = session.get("username")

    try:
        with hold_operation_lock(
            db,
            key="maintenance:phenotype-backfill:all-users",
            actor=username,
            label="Global phenotype cache backfill",
            ttl_seconds=3600,
            metadata={"route": "backfill_all_phenotype_cache", "scope": "all_users"},
            conflict_message="A global phenotype cache backfill is already running. Please wait for it to finish before retrying.",
        ):
            report = _run_phenotype_cache_backfill()
            write_activity(username, "Backfilled phenotype cache for all users", db)
        flash(
            _build_phenotype_cache_backfill_message(report, scope_label="all users"),
            "success",
        )
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=url_for("settings.admin_settings"))
    except Exception as exc:
        current_app.logger.exception("Error backfilling phenotype cache for all users: %s", exc)
        flash(f"Error backfilling phenotype caches for all users: {str(exc)}", "error")

    return redirect(url_for("settings.admin_settings"))


@bp.route("/settings/refresh-all-provider-caches", methods=["POST"])
@login_required
@admin_required
@limiter.limit("1 per hour")
def refresh_all_provider_caches():
    username = session.get("username")
    redirect_to = request.referrer or url_for("main.home")

    try:
        with hold_operation_lock(
            db,
            key="maintenance:provider-cache-refresh:all-stocks",
            actor=username,
            label="Global provider cache refresh",
            ttl_seconds=3600,
            metadata={"route": "refresh_all_provider_caches", "scope": "all_stocks"},
            conflict_message="A global provider cache refresh is already running. Please wait for it to finish before retrying.",
        ):
            report = _run_provider_match_cache_refresh()
            write_activity(username, "Refreshed provider match cache for all stocks", db)
        flash(_build_provider_match_cache_refresh_message(report), "success")
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=redirect_to)
    except Exception as exc:
        current_app.logger.exception("Error refreshing provider caches for all stocks: %s", exc)
        flash(f"Error refreshing provider caches for all stocks: {str(exc)}", "error")

    return redirect(redirect_to)


# Add this route to your existing settings blueprint
@bp.route("/update-bloomington-stock", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_bloomington_stock():
    """Manual trigger for refreshing the legacy Bloomington compatibility dataset."""
    try:
        with hold_operation_lock(
            db,
            key="maintenance:bloomington-stock-refresh",
            actor=session.get("username"),
            label="Legacy Bloomington refresh",
            ttl_seconds=3600,
            metadata={"route": "update_bloomington_stock"},
            conflict_message="The legacy Bloomington CSV refresh is already running. Please wait for it to finish before retrying.",
        ):
            bloomington_service.manual_update_bloomington_stock(
                current_app._get_current_object()
            )
        flash("Legacy Bloomington compatibility refresh initiated successfully!", "success")
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=url_for("settings.admin_settings"))
    except Exception as e:
        flash(f"Error refreshing legacy Bloomington compatibility data: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))


@bp.route("/update-gene-metadata", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_gene_metadata():
    """Manual trigger for updating gene metadata from the legacy Bloomington CSV."""
    try:
        with hold_operation_lock(
            db,
            key="maintenance:bloomington-gene-metadata-refresh",
            actor=session.get("username"),
            label="Legacy Bloomington gene metadata refresh",
            ttl_seconds=1800,
            metadata={"route": "update_gene_metadata"},
            conflict_message="The legacy Bloomington gene metadata refresh is already running. Please wait for it to finish before retrying.",
        ):
            bloomington_service.manual_update_gene_metadata_only(
                current_app._get_current_object()
            )
        flash("Legacy Bloomington gene metadata update initiated successfully!", "success")
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=url_for("settings.admin_settings"))
    except Exception as e:
        flash(f"Error updating legacy Bloomington gene metadata: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))


@bp.route("/update-flybase-gene-metadata", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_flybase_gene_metadata():
    """Manual trigger for updating gene metadata from compatible FlyBase stock records."""
    try:
        with hold_operation_lock(
            db,
            key="maintenance:flybase-gene-metadata-refresh",
            actor=session.get("username"),
            label="FlyBase gene metadata refresh",
            ttl_seconds=1800,
            metadata={"route": "update_flybase_gene_metadata"},
            conflict_message="The FlyBase gene metadata refresh is already running. Please wait for it to finish before retrying.",
        ):
            flybase_service.manual_update_flybase_gene_metadata_only(
                current_app._get_current_object()
            )
        flash("FlyBase gene metadata update initiated successfully!", "success")
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=url_for("settings.admin_settings"))
    except Exception as e:
        flash(f"Error updating FlyBase gene metadata: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))


@bp.route("/refresh-flybase-reference-data", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def refresh_flybase_reference_data():
    """Manual trigger for release-aware FlyBase dataset refresh and stock metadata regeneration."""
    try:
        with hold_operation_lock(
            db,
            key="maintenance:flybase-reference-refresh",
            actor=session.get("username"),
            label="FlyBase reference refresh",
            ttl_seconds=7200,
            metadata={"route": "refresh_flybase_reference_data"},
            conflict_message="A FlyBase reference refresh is already running. Please wait for it to finish before retrying.",
        ):
            report = flybase_service.manual_refresh_flybase_reference_data(
                current_app._get_current_object()
            )
        downloaded_count = sum(
            1
            for item in report["download_report"]["results"]
            if item.get("status") == "downloaded"
        )
        skipped_count = sum(
            1
            for item in report["download_report"]["results"]
            if item.get("status") == "skipped"
        )
        flash(
            f"FlyBase release {report['release']} refresh completed: {downloaded_count} files downloaded, {skipped_count} reused, gene metadata refreshed.",
            "success",
        )
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=url_for("settings.admin_settings"))
    except Exception as e:
        current_app.logger.exception("Error refreshing FlyBase reference data: %s", e)
        flash(f"Error refreshing FlyBase reference data: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))
