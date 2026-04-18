import io
import os
from datetime import datetime  # Added import

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.utils import secure_filename

# Import necessary components from app context and utils
from flymanager.app import allowed_file, db  # Import helper from app context
from flymanager.app.routes.auth import admin_required, login_required
from flymanager.app.security import limiter
from flymanager.app.services import flybase as flybase_service
from flymanager.utils.converter import mongo_to_xls, xls_to_mongo
from flymanager.utils.mongo import write_activity  # Import for logging

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
    """Generates and downloads an Excel file of the user's data."""
    username = session.get("username")
    output = io.BytesIO()

    try:
        # Generate Excel file in memory using the utility function
        # Pass db, output buffer, and username
        mongo_to_xls(db, output)
        output.seek(0) # Rewind the buffer

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        download_name = f'fly_manager_data_{username}_{timestamp}.xlsx'

        # Log activity
        write_activity(username, 'Downloaded data to Excel', db)

        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=download_name
        )
    except Exception as e:
        current_app.logger.exception("Error generating Excel download for %s: %s", username, e)
        import traceback
        traceback.print_exc()
        flash(f"An error occurred while generating the Excel file: {e}", "error")
        # Redirect to a page where flash message can be seen, e.g., home or data upload page
        return redirect(url_for('.upload_data_route')) # Redirect to upload page within the same blueprint


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
