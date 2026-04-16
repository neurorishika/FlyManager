from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from flymanager.app import db
from flymanager.app.routes.auth import admin_required, login_required
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_optional_text)
from flymanager.app.services import bloomington as bloomington_service
from flymanager.app.services import flybase as flybase_service
from flymanager.utils.mongo import (get_settings, update_settings,
                                    write_activity)

bp = Blueprint("settings", __name__)


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
    return render_template("settings/admin.html", settings=settings)


# Add this route to your existing settings blueprint
@bp.route("/update-bloomington-stock", methods=["POST"])
@login_required
@admin_required
@limiter.limit("2 per hour")
def update_bloomington_stock():
    """Manual trigger for refreshing the legacy Bloomington compatibility dataset."""
    try:
        bloomington_service.manual_update_bloomington_stock(
            current_app._get_current_object()
        )
        flash("Legacy Bloomington compatibility refresh initiated successfully!", "success")
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
        bloomington_service.manual_update_gene_metadata_only(
            current_app._get_current_object()
        )
        flash("Legacy Bloomington gene metadata update initiated successfully!", "success")
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
        flybase_service.manual_update_flybase_gene_metadata_only(
            current_app._get_current_object()
        )
        flash("FlyBase gene metadata update initiated successfully!", "success")
    except Exception as e:
        flash(f"Error updating FlyBase gene metadata: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))
