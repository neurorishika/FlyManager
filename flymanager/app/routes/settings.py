from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from flymanager.app import db
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.routes.auth import admin_required, login_required
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_optional_text)
from flymanager.app.services import flybase as flybase_service
from flymanager.utils.mongo import (OperationLockConflict, get_settings,
                                    get_user_profiles,
                                    update_settings,
                                    update_user_reporting_manager,
                                    write_activity)

bp = Blueprint("settings", __name__)


def _handle_locked_form_submission(conflict_error, *, redirect_to):
    flash(str(conflict_error), "warning")
    return redirect(redirect_to)


def _enqueue_or_flash_conflict(*, redirect_to, started_message, key, func, task_kwargs=None, **enqueue_kwargs):
    """Enqueue a background job whose task function's first arg is the job key.

    Every task function in flymanager.app.jobs.tasks takes `key` as its first
    argument (used to update the job-status record as it runs), so this
    helper wires that up once instead of repeating it at every call site.
    """
    try:
        enqueue_job(
            db,
            key=key,
            func=func,
            kwargs={"key": key, **(task_kwargs or {})},
            **enqueue_kwargs,
        )
    except OperationLockConflict as exc:
        return _handle_locked_form_submission(exc, redirect_to=redirect_to)
    flash(started_message, "info")
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

    return _enqueue_or_flash_conflict(
        redirect_to=redirect_to,
        started_message="Phenotype cache backfill for your records started in the background.",
        key=f"maintenance:phenotype-backfill:user:{username}",
        actor=username,
        label="Phenotype cache backfill (your records)",
        func=job_tasks.task_backfill_phenotype_cache,
        task_kwargs={"username": username, "users": [username], "scope_label": "your maintained records"},
        ttl_seconds=1800,
        metadata={"route": "backfill_my_phenotype_cache", "scope": "owned_records"},
        conflict_message="Your phenotype cache backfill is already running. Please wait for it to finish before starting it again.",
    )


@bp.route("/settings/backfill-all-phenotype-cache", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def backfill_all_phenotype_cache():
    username = session.get("username")

    return _enqueue_or_flash_conflict(
        redirect_to=url_for("settings.admin_settings"),
        started_message="Phenotype cache backfill for all users started in the background.",
        key="maintenance:phenotype-backfill:all-users",
        actor=username,
        label="Global phenotype cache backfill",
        func=job_tasks.task_backfill_phenotype_cache,
        task_kwargs={"username": username, "users": None, "scope_label": "all users"},
        ttl_seconds=3600,
        metadata={"route": "backfill_all_phenotype_cache", "scope": "all_users"},
        conflict_message="A global phenotype cache backfill is already running. Please wait for it to finish before retrying.",
    )


@bp.route("/settings/refresh-all-provider-caches", methods=["POST"])
@login_required
@admin_required
@limiter.limit("1 per hour")
def refresh_all_provider_caches():
    username = session.get("username")
    redirect_to = request.referrer or url_for("main.home")

    return _enqueue_or_flash_conflict(
        redirect_to=redirect_to,
        started_message="Provider cache refresh for all stocks started in the background.",
        key="maintenance:provider-cache-refresh:all-stocks",
        actor=username,
        label="Global provider cache refresh",
        func=job_tasks.task_refresh_provider_caches,
        task_kwargs={"username": username},
        ttl_seconds=3600,
        metadata={"route": "refresh_all_provider_caches", "scope": "all_stocks"},
        conflict_message="A global provider cache refresh is already running. Please wait for it to finish before retrying.",
    )


# Add this route to your existing settings blueprint
@bp.route("/update-bloomington-stock", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_bloomington_stock():
    """Manual trigger for refreshing the legacy Bloomington compatibility dataset."""
    username = session.get("username")
    return _enqueue_or_flash_conflict(
        redirect_to=url_for("settings.admin_settings"),
        started_message="Legacy Bloomington compatibility refresh started in the background.",
        key="maintenance:bloomington-stock-refresh",
        actor=username,
        label="Legacy Bloomington refresh",
        func=job_tasks.task_update_bloomington_stock,
        task_kwargs={"username": username},
        ttl_seconds=3600,
        metadata={"route": "update_bloomington_stock"},
        conflict_message="The legacy Bloomington CSV refresh is already running. Please wait for it to finish before retrying.",
    )


@bp.route("/update-gene-metadata", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_gene_metadata():
    """Manual trigger for updating gene metadata from the legacy Bloomington CSV."""
    username = session.get("username")
    return _enqueue_or_flash_conflict(
        redirect_to=url_for("settings.admin_settings"),
        started_message="Legacy Bloomington gene metadata update started in the background.",
        key="maintenance:bloomington-gene-metadata-refresh",
        actor=username,
        label="Legacy Bloomington gene metadata refresh",
        func=job_tasks.task_update_gene_metadata,
        task_kwargs={"username": username},
        ttl_seconds=1800,
        metadata={"route": "update_gene_metadata"},
        conflict_message="The legacy Bloomington gene metadata refresh is already running. Please wait for it to finish before retrying.",
    )


@bp.route("/update-flybase-gene-metadata", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_flybase_gene_metadata():
    """Manual trigger for updating gene metadata from compatible FlyBase stock records."""
    username = session.get("username")
    return _enqueue_or_flash_conflict(
        redirect_to=url_for("settings.admin_settings"),
        started_message="FlyBase gene metadata update started in the background.",
        key="maintenance:flybase-gene-metadata-refresh",
        actor=username,
        label="FlyBase gene metadata refresh",
        func=job_tasks.task_update_flybase_gene_metadata,
        task_kwargs={"username": username},
        ttl_seconds=1800,
        metadata={"route": "update_flybase_gene_metadata"},
        conflict_message="The FlyBase gene metadata refresh is already running. Please wait for it to finish before retrying.",
    )


@bp.route("/refresh-flybase-reference-data", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def refresh_flybase_reference_data():
    """Manual trigger for release-aware FlyBase dataset refresh and stock metadata regeneration."""
    username = session.get("username")
    return _enqueue_or_flash_conflict(
        redirect_to=url_for("settings.admin_settings"),
        started_message="FlyBase reference data refresh started in the background. This can take a while for a new release.",
        key="maintenance:flybase-reference-refresh",
        actor=username,
        label="FlyBase reference refresh",
        func=job_tasks.task_refresh_flybase_reference_data,
        task_kwargs={"username": username},
        ttl_seconds=7200,
        metadata={"route": "refresh_flybase_reference_data"},
        conflict_message="A FlyBase reference refresh is already running. Please wait for it to finish before retrying.",
    )
