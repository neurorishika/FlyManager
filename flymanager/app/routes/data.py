import os
from datetime import datetime  # Added import

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.utils import secure_filename

# Import necessary components from app context and utils
from flymanager.app import allowed_file, db  # Import helper from app context
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.jobs.tasks import EXPORT_SUBDIR
from flymanager.app.routes.auth import admin_required, login_required
from flymanager.app.security import limiter
from flymanager.app.services import flybase as flybase_service
from flymanager.utils.converter import xls_to_mongo
from flymanager.utils.mongo import (OperationLockConflict, get_job_status,
                                    write_activity)  # Import for logging

# Define the Blueprint
bp = Blueprint('data', __name__, url_prefix='/data') # url_prefix defined in app/__init__


def _get_flybase_reference_status():
    try:
        return flybase_service.get_flybase_reference_status(
            current_app._get_current_object()
        )
    except Exception as exc:
        current_app.logger.warning(
            'Unable to load FlyBase sync status for data operations page: %s',
            exc,
        )
        return None

@bp.route('/download')
@login_required
@admin_required
@limiter.limit('10 per hour')
def download_data_route():
    """Starts a background export of all data to Excel; the file is fetched via
    download_data_file_route once the job (visible in the jobs banner /
    Settings > Jobs) finishes."""
    username = session.get("username")
    key = f"maintenance:excel-export:user:{username}"

    try:
        enqueue_job(
            db,
            key=key,
            actor=username,
            label="Excel data export",
            func=job_tasks.task_export_excel,
            kwargs={"key": key, "username": username},
            ttl_seconds=1800,
            metadata={"route": "download_data_route"},
            conflict_message="An Excel export for you is already running. Please wait for it to finish.",
        )
    except OperationLockConflict as exc:
        flash(str(exc), "warning")
        return redirect(url_for('.upload_data_route'))

    flash("Generating your Excel export in the background - it'll be ready to download shortly.", "info")
    return redirect(url_for('.upload_data_route'))


@bp.route('/download/<path:job_key>/file')
@login_required
@admin_required
def download_data_file_route(job_key):
    """Serves the file produced by a finished Excel export job."""
    job = get_job_status(db, job_key)
    if not job or not job.get("result") or not job["result"].get("download_filename"):
        flash("That export isn't ready yet (or has expired). Please start a new one.", "warning")
        return redirect(url_for('.upload_data_route'))

    file_path = os.path.join(EXPORT_SUBDIR, job["result"]["download_filename"])
    if not os.path.exists(file_path):
        flash("That export file is no longer available. Please start a new one.", "warning")
        return redirect(url_for('.upload_data_route'))

    return send_file(
        file_path,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=job["result"]["download_filename"],
    )


@bp.route('/upload', methods=['GET', 'POST'])
@login_required
@admin_required
@limiter.limit('5 per hour')
def upload_data_route():
    """Handles uploading an Excel file to import/update data."""

    username = session.get("username")

    if request.method == 'POST':
        # --- File Handling ---
        if 'file' not in request.files:
            flash('No file part in request.', 'error')
            return redirect(request.url)

        file = request.files['file']

        if file.filename == '':
            flash('No file selected.', 'warning')
            return redirect(request.url)

        if not file or not allowed_file(file.filename):
            flash('Invalid file type. Only .xlsx files are allowed.', 'error')
            return redirect(request.url)

        # --- File Processing ---
        filename = secure_filename(file.filename)
        if not filename:
            flash('Invalid file name.', 'error')
            return redirect(request.url)

        upload_folder = current_app.config['UPLOAD_FOLDER']
        # Ensure the upload folder exists (though it should be created by create_app)
        os.makedirs(upload_folder, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_path = os.path.join(upload_folder, f'{timestamp}_{filename}')

        try:
            file.save(file_path)
            current_app.logger.info('Saved uploaded workbook for %s to %s', username, file_path)

            # Process the Excel file and update MongoDB using the utility function
            # Pass db, file path, and username
            # Consider adding return values to xls_to_mongo for more detailed feedback
            xls_to_mongo(file_path, db)

            # Log activity
            write_activity(username, f'Uploaded data from file: {filename}', db)

            flash('Data successfully uploaded and swapped into place.', 'success')
            # Redirect to home or explorer page after successful upload
            return redirect(url_for('main.home')) # Redirect to main home page

        except Exception as e:
            current_app.logger.exception(
                'Error processing uploaded file %s for user %s: %s',
                filename,
                username,
                e,
            )
            import traceback
            traceback.print_exc()
            flash(f'An error occurred while processing the file: {e}', 'error')
            # Keep the file for debugging? For now, remove it.
            # Consider adding more specific error handling based on xls_to_mongo exceptions
            return redirect(request.url) # Redirect back to upload page on error

        finally:
            # Ensure temporary file is removed after processing attempt
            if os.path.exists(file_path):
                 try:
                     os.remove(file_path)
                     current_app.logger.info('Removed temporary file %s', file_path)
                 except OSError as remove_err:
                     current_app.logger.warning(
                         'Error removing temporary file %s: %s', file_path, remove_err
                     )

    # For GET request, just render the upload form
    return render_template(
        'utilities/upload_data.html',
        username=username,
        flybase_reference_status=_get_flybase_reference_status(),
    )
