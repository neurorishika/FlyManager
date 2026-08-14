from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from flymanager.app import db
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.routes.auth import login_required
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_identifier_list,
                                     normalize_optional_text, parse_int_value)
from flymanager.utils.mongo import (OperationLockConflict, add_tray,
                                    bulk_update_document_assignments,
                                    calculate_required_vials, delete_tray,
                                    get_accessible_crosses,
                                    get_accessible_stock,
                                    get_accessible_stocks, get_accessible_tray,
                                    get_accessible_trays, get_direct_reports,
                                    get_tray, get_tray_occupancy,
                                    get_tray_occupancies_bulk,
                                    hold_operation_locks, move_item_to_tray,
                                    record_operation_lock_keys, update_tray,
                                    write_activity)

# Create blueprint
bp = Blueprint('tray', __name__)

@bp.route('/trays')
@login_required
def tray_management():
    """
    Render the tray management page.
    """
    # Get database connection
    
    user = session.get('username')
    
    trays = get_accessible_trays(user, db)
    direct_reports = get_direct_reports(user, db)
    occupancies = get_tray_occupancies_bulk(trays, db)

    for tray in trays:
        occupancy = occupancies.get(tray["UniqueID"], {})
        tray["OccupiedStarts"] = len(
            [item for item in occupancy.values() if item.get("type") != "blocked"]
        )
        tray["BlockedCells"] = len(
            [item for item in occupancy.values() if item.get("type") == "blocked"]
        )

    # Render the tray management page
    return render_template(
        'tray/tray_management.html', 
        trays=trays,
        direct_reports=direct_reports,
        page_title="Tray Management",
        username=user
    )


@bp.route('/assign_tray/<tray_id>', methods=['POST'])
@login_required
@limiter.limit('20 per hour')
def assign_tray_route(tray_id):
    user = session.get('username')
    assignee = normalize_optional_text(
        request.form.get('assignee'),
        field_name='Assignee',
        max_length=32,
    )

    tray = get_tray(user, tray_id, db)
    if not tray:
        flash('Only the tray owner can update tray assignments.', 'error')
        return redirect(url_for('tray.tray_management'))

    assignment_targets = []
    for collection_name in ('stocks', 'crosses'):
        for document in db[collection_name].find({'User': user, 'TrayID': tray['TrayID']}):
            if document.get('Status') == 'No longer maintained':
                continue
            assignment_targets.append((collection_name, document['UniqueID']))

    if not assignment_targets:
        flash(f"Tray {tray['TrayID']} has no active stocks or crosses to assign.", 'error')
        return redirect(url_for('tray.tray_management'))

    updated_count, error_message = bulk_update_document_assignments(
        user, assignee, assignment_targets, db,
    )
    if error_message:
        flash(error_message, 'error')
        return redirect(url_for('tray.tray_management'))

    if assignee:
        flash(
            f"Assigned {updated_count} tray item{'s' if updated_count != 1 else ''} in {tray['TrayID']} to {assignee}.",
            'success',
        )
    else:
        flash(
            f"Returned {updated_count} tray item{'s' if updated_count != 1 else ''} in {tray['TrayID']} to owner maintenance.",
            'success',
        )

    write_activity(user, f"Updated tray assignments for {tray['TrayID']}", db)
    return redirect(url_for('tray.tray_management'))

@bp.route('/tray/<tray_id>')
@login_required
def view_tray(tray_id):
    """
    View a specific tray.
    """
    # Get database connection
    
    user = session.get('username')
    
    # Get tray and its occupancy
    tray = get_accessible_tray(user, tray_id, db)
    if not tray:
        flash(f"Tray {tray_id} not found", "error")
        return redirect(url_for('tray.tray_management'))
    
    tray_owner = tray.get('User', user)
    occupancy = get_tray_occupancy(tray_owner, tray['TrayID'], db)
    occupied_positions = sorted(
        (
            (position, item)
            for position, item in occupancy.items()
            if item.get('type') != 'blocked'
        ),
        key=lambda entry: int(entry[0])
    )
    
    # Get all stocks and crosses for this user (for moving items)
    stocks = [
        stock for stock in get_accessible_stocks(user, db)
        if stock.get('User') == tray_owner
    ]
    crosses = [
        cross for cross in get_accessible_crosses(user, db)
        if cross.get('User') == tray_owner
    ]
    
    # Filter out stocks and crosses that are no longer maintained
    stocks = [stock for stock in stocks if stock["Status"] != "No longer maintained"]
    crosses = [cross for cross in crosses if cross["Status"] != "No longer maintained"]
    
    # For each item, calculate how many vials it needs
    for stock in stocks:
        stock["required_vials"] = calculate_required_vials(stock)
    
    for cross in crosses:
        cross["required_vials"] = calculate_required_vials(cross)
    
    return render_template(
        'tray/view_tray.html',
        tray=tray,
        occupancy=occupancy,
        occupied_positions=occupied_positions,
        stocks=stocks,
        crosses=crosses,
        owner_can_edit=tray_owner == user,
        page_title=f"Tray: {tray['TrayID']} - {tray['Name']}",
        username=user
    )

@bp.route('/add_tray', methods=['GET', 'POST'])
@login_required
@limiter.limit('20 per hour')
def add_tray_route():
    """
    Add a new tray.
    """
    if request.method == 'POST':
        # Get database connection
        
        user = session.get('username')
        
        # Get form data
        tray_id = request.form.get('tray_id')
        name = request.form.get('name')
        rows = parse_int_value(request.form.get('rows', 10), field_name='Rows', minimum=1, maximum=100)
        columns = parse_int_value(request.form.get('columns', 10), field_name='Columns', minimum=1, maximum=100)
        description = request.form.get('description', '')
        
        # Create tray properties
        properties = {
            "TrayID": tray_id,
            "Name": name,
            "Rows": rows,
            "Columns": columns,
            "Description": description
        }
        
        # Add tray to database
        success, result = add_tray(user, properties, db)
        if success:
            # Log activity
            write_activity(user, f"Added new tray with TrayID: {tray_id}", db)
            flash(f"Tray {tray_id} added successfully", "success")
            return redirect(url_for('tray.view_tray', tray_id=result))
        else:
            flash(f"Failed to add tray: {result}", "error")
            return redirect(url_for('tray.tray_management'))
    
    # GET request, show the add tray form
    return render_template('tray/add_tray.html', page_title="Add New Tray", username=session.get('username'))

@bp.route('/edit_tray/<tray_id>', methods=['GET', 'POST'])
@login_required
@limiter.limit('20 per hour')
def edit_tray_route(tray_id):
    """
    Edit an existing tray.
    """
    # Get database connection
    
    user = session.get('username')
    
    # Get the tray
    tray = get_tray(user, tray_id, db)
    if not tray:
        flash(f"Tray {tray_id} not found", "error")
        return redirect(url_for('tray.tray_management'))
    
    if request.method == 'POST':
        # Get form data
        name = request.form.get('name')
        rows = parse_int_value(request.form.get('rows'), field_name='Rows', minimum=1, maximum=100)
        columns = parse_int_value(request.form.get('columns'), field_name='Columns', minimum=1, maximum=100)
        description = request.form.get('description', '')
        
        # Create updates dictionary
        updates = {
            "Name": name,
            "Rows": int(rows),
            "Columns": int(columns),
            "Description": description
        }
        
        # Update tray in database
        success = update_tray(user, tray_id, updates, db)
        if success:
            # Log activity
            write_activity(user, f"Updated tray with TrayID: {tray_id}", db)
            flash(f"Tray {tray_id} updated successfully", "success")
            return redirect(url_for('tray.view_tray', tray_id=tray['UniqueID']))
        else:
            flash(f"Failed to update tray", "error")
    
    # For GET or failed POST, show edit form with current values
    return render_template('tray/edit_tray.html', tray=tray, page_title=f"Edit Tray {tray_id}", username=user)

@bp.route('/delete_tray/<tray_id>', methods=['POST'])
@login_required
@limiter.limit('10 per hour')
def delete_tray_route(tray_id):
    """
    Delete a tray.
    """
    # Get database connection
    
    user = session.get('username')
    
    # Check if tray exists
    tray = get_tray(user, tray_id, db)
    if not tray:
        flash(f"Tray {tray_id} not found", "error")
        return redirect(url_for('tray.tray_management'))
    
    # Check if tray is empty
    occupancy = get_tray_occupancy(user, tray_id, db)
    if occupancy:
        flash("Cannot delete tray that contains items", "error")
        return redirect(url_for('tray.view_tray', tray_id=tray_id))
    
    # Delete tray
    success = delete_tray(user, tray_id, db)
    if success:
        # Log activity
        write_activity(user, f"Deleted tray with TrayID: {tray_id}", db)
        flash(f"Tray {tray_id} deleted successfully", "success")
    else:
        flash("Failed to delete tray", "error")
    
    return redirect(url_for('tray.tray_management'))

@bp.route('/move_to_tray_route', methods=['POST'])
@login_required
@limiter.limit('30 per minute')
def move_to_tray_route():
    """
    Move a stock or cross to a specific tray position.
    """
    # Get data from request
    try:
        data = get_json_payload()
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)})

    item_type = normalize_optional_text(data.get('item_type'), field_name='Item type', max_length=16)
    item_id = normalize_optional_text(data.get('item_id'), field_name='Item ID', max_length=64)
    tray_id = normalize_optional_text(data.get('tray_id'), field_name='Tray ID', max_length=64)
    position = normalize_optional_text(data.get('position'), field_name='Position', max_length=16)
    
    # Validate required fields
    if not all([item_type, item_id]):
        return jsonify({"success": False, "message": "Missing item type or ID"})
    if item_type not in {'stock', 'cross'}:
        return jsonify({"success": False, "message": "Invalid item type"})
    
    # For removal from tray, tray_id and position should be empty strings
    is_removal = tray_id == '' and position == ''
    
    # For moving to a tray, both tray_id and position are required
    if not is_removal and not all([tray_id, position]):
        return jsonify({"success": False, "message": "Missing tray ID or position"})
    
    # Get database connection
    user = session.get('username')
    
    # Move item to tray (or remove if tray_id and position are empty)
    success = move_item_to_tray(user, item_type, item_id, tray_id, position, db)
    
    if success:
        # Log activity
        action = "Removed" if is_removal else "Moved"
        location = "from tray" if is_removal else f"to tray {tray_id}, position {position}"
        write_activity(
            user, 
            f"{action} {item_type} with {item_id} {location}", 
            db
        )
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "message": "Failed to move item"})

@bp.route('/bulk_remove_from_tray', methods=['POST'])
@login_required
@limiter.limit('20 per minute')
def bulk_remove_from_tray():
    """Queues a background job that removes the selected items from their trays."""
    try:
        data = get_json_payload()
        item_type = normalize_optional_text(data.get('item_type'), field_name='Item type', max_length=16)
        unique_ids = normalize_identifier_list(data.get('uniqueIDs', []), field_name='uniqueIDs')
    except ValueError as exc:
        return jsonify({"success": False, "queued": False, "message": str(exc)}), 400
    if item_type not in {'stock', 'cross'}:
        return jsonify({"success": False, "queued": False, "message": "Invalid item type"}), 400
    if not unique_ids:
        return jsonify({"success": False, "queued": False, "message": "No items selected."}), 400

    user = session.get('username')
    key = f"bulk-remove-from-tray:user:{user}"
    try:
        enqueue_job(
            db,
            key=key,
            actor=user,
            label=f"Bulk remove from tray ({len(unique_ids)} items)",
            func=job_tasks.task_bulk_remove_from_tray,
            kwargs={
                "key": key,
                "username": user,
                "item_type": item_type,
                "uids": unique_ids,
            },
            ttl_seconds=1800,
            metadata={"route": "bulk_remove_from_tray", "uid_count": len(unique_ids), "item_type": item_type},
            conflict_message="A tray removal for you is already running. Please wait for it to finish before starting another.",
        )
    except OperationLockConflict as exc:
        return jsonify({"success": False, "queued": False, "message": str(exc)}), 409

    return (
        jsonify({
            "success": True,
            "queued": True,
            "job": key,
            "message": f"Removing {len(unique_ids)} item{'s' if len(unique_ids) != 1 else ''} from trays in the background. Watch the jobs banner for progress.",
        }),
        202,
    )

# API endpoint to get tray occupancy data for the UI
@bp.route('/api/tray/<tray_id>/occupancy')
@login_required
def get_tray_occupancy_api(tray_id):
    """
    Get tray occupancy data in JSON format for the UI.
    """
    
    user = session.get('username')
    
    # Get tray data
    tray = get_accessible_tray(user, tray_id, db)
    if not tray:
        return jsonify({"success": False, "message": "Tray not found"})
        
    # Get occupancy data
    occupancy = get_tray_occupancy(tray.get('User', user), tray['TrayID'], db)
    
    # Return the data
    return jsonify({
        "success": True,
        "tray": {
            "id": tray["TrayID"],
            "name": tray["Name"],
            "rows": tray["Rows"],
            "columns": tray["Columns"],
        },
        "occupancy": occupancy
    })