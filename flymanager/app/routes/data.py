import os
import io
from datetime import datetime # Added import
from flask import (
    Blueprint, render_template, request, redirect, session,
    flash, url_for, current_app, send_file
)
from werkzeug.utils import secure_filename

# Import necessary components from app context and utils
from flymanager.app import db, allowed_file # Import helper from app context
from flymanager.utils.converter import mongo_to_xls, xls_to_mongo
from flymanager.utils.mongo import write_activity # Import for logging
from flymanager.app.routes.auth import login_required
# Define the Blueprint
bp = Blueprint('data', __name__, url_prefix='/data') # url_prefix defined in app/__init__

@bp.route('/download')
@login_required
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
        print(f"Error generating Excel download for {username}: {e}")
        import traceback
        traceback.print_exc()
        flash(f"An error occurred while generating the Excel file: {e}", "error")
        # Redirect to a page where flash message can be seen, e.g., home or data upload page
        return redirect(url_for('.upload_data_route')) # Redirect to upload page within the same blueprint


@bp.route('/upload', methods=['GET', 'POST'])
@login_required
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
        upload_folder = current_app.config['UPLOAD_FOLDER']
        # Ensure the upload folder exists (though it should be created by create_app)
        os.makedirs(upload_folder, exist_ok=True)
        file_path = os.path.join(upload_folder, filename)

        try:
            file.save(file_path)
            print(f"File saved temporarily to {file_path}")

            # Process the Excel file and update MongoDB using the utility function
            # Pass db, file path, and username
            # Consider adding return values to xls_to_mongo for more detailed feedback
            xls_to_mongo(file_path, db)

            # Log activity
            write_activity(username, f'Uploaded data from file: {filename}', db)

            flash('Data successfully uploaded and processed.', 'success')
            # Redirect to home or explorer page after successful upload
            return redirect(url_for('main.home')) # Redirect to main home page

        except Exception as e:
            print(f"Error processing uploaded file {filename} for user {username}: {e}")
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
                     print(f"Removed temporary file: {file_path}")
                 except OSError as remove_err:
                     print(f"Error removing temporary file {file_path}: {remove_err}")

    # For GET request, just render the upload form
    return render_template('upload_data.html', username=username)
