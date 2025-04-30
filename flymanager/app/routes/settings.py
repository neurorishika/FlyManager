from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flymanager.app import db
from flymanager.utils.mongo import update_settings, write_activity, get_settings
from flymanager.app.routes.auth import login_required

bp = Blueprint('settings', __name__)

@bp.route('/settings', methods=['GET', 'POST'])
@login_required
def admin_settings():
    if session.get('username') != 'admin':
        flash('Only administrators can access settings.', 'error')
        return redirect(url_for('main.home'))

    if request.method == 'POST':
        if request.is_json:
            # Handle AJAX requests for theme toggle
            updates = request.get_json()
            if update_settings(updates, db):
                return jsonify({'status': 'success'})
            return jsonify({'status': 'error'}), 500

        # Handle form submissions
        updates = {
            'theme': {
                'dark_mode': bool(request.form.get('dark_mode')),
                'accent_color': request.form.get('accent_color')
            },
            'lab_info': {
                'lab_name': request.form.get('lab_name'),
                'admin_name': request.form.get('admin_name'),
                'admin_email': request.form.get('admin_email')
            }
        }
        
        if update_settings(updates, db):
            write_activity('admin', 'Updated application settings', db)
            flash('Settings updated successfully.', 'success')
        else:
            flash('Failed to update settings.', 'error')
        
        return redirect(url_for('settings.admin_settings'))

    settings = get_settings(db)
    return render_template('settings/admin.html', settings=settings)