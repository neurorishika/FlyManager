from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
)
from flymanager.app import db
from flymanager.utils.mongo import update_settings, write_activity, get_settings
from flymanager.app.routes.auth import login_required, admin_required
from flymanager.app.services import bloomington as bloomington_service

bp = Blueprint("settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def admin_settings():
    if session.get("username") != "admin":
        flash("Only administrators can access settings.", "error")
        return redirect(url_for("main.home"))

    if request.method == "POST":
        if request.is_json:
            # Handle AJAX requests for theme toggle
            updates = request.get_json()
            if update_settings(updates, db):
                return jsonify({"status": "success"})
            return jsonify({"status": "error"}), 500

        # Handle form submissions
        updates = {
            "theme": {
                "dark_mode": bool(request.form.get("dark_mode")),
                "accent_color": request.form.get("accent_color"),
            },
            "lab_info": {
                "lab_name": request.form.get("lab_name"),
                "admin_name": request.form.get("admin_name"),
                "admin_email": request.form.get("admin_email"),
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
def update_bloomington_stock():
    """Manual trigger for updating Bloomington stock data."""
    try:
        from flask import current_app

        bloomington_service.manual_update_bloomington_stock(
            current_app._get_current_object()
        )
        flash("Bloomington stock data update initiated successfully!", "success")
    except Exception as e:
        flash(f"Error updating Bloomington stock data: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))


@bp.route("/update-gene-metadata", methods=["POST"])
@login_required
@admin_required
def update_gene_metadata():
    """Manual trigger for updating gene metadata from existing Bloomington data."""
    try:
        from flask import current_app

        bloomington_service.manual_update_gene_metadata_only(
            current_app._get_current_object()
        )
        flash("Gene metadata update initiated successfully!", "success")
    except Exception as e:
        flash(f"Error updating gene metadata: {str(e)}", "error")

    return redirect(url_for("settings.admin_settings"))
