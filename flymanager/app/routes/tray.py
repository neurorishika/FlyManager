from flask import (Blueprint, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from flymanager.app import db
from flymanager.app.routes.auth import login_required
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_identifier_list,
                                     normalize_optional_text, parse_int_value)
from flymanager.utils.mongo import (add_tray, calculate_required_vials,
                                    delete_tray, get_database, get_tray,
                                    get_tray_occupancy, get_user_crosses,
                                    get_user_stocks, get_user_trays,
                                    move_item_to_tray, update_tray,
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
    
    # Get user's trays
    trays = get_user_trays(user, db)
    
    # Get all stocks and crosses for this user (for moving items)
    stocks = get_user_stocks(user, db)
    crosses = get_user_crosses(user, db)
    
    # Filter out stocks and crosses that are no longer maintained
    stocks = [stock for stock in stocks if stock["Status"] != "No longer maintained"]
    crosses = [cross for cross in crosses if cross["Status"] != "No longer maintained"]
    
    # Render the tray management page
    return render_template(
        'tray/tray_management.html', 
        trays=trays,
        stocks=stocks,
        crosses=crosses,
        page_title="Tray Management",
        username=user
    )

@bp.route('/tray/<tray_id>')
@login_required
def view_tray(tray_id):
    """
    View a specific tray.
    """
    # Get database connection
    
    user = session.get('username')
    
    # Get tray and its occupancy
    tray = get_tray(user, tray_id, db)
    if not tray:
        flash(f"Tray {tray_id} not found", "error")
        return redirect(url_for('tray.tray_management'))
    
    occupancy = get_tray_occupancy(user, tray_id, db)
    occupied_positions = sorted(
        (
            (position, item)
            for position, item in occupancy.items()
            if item.get('type') != 'blocked'
        ),
        key=lambda entry: int(entry[0])
    )
    
    # Get all stocks and crosses for this user (for moving items)
    stocks = get_user_stocks(user, db)
    crosses = get_user_crosses(user, db)
    
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
            return redirect(url_for('tray.view_tray', tray_id=tray_id))
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
            return redirect(url_for('tray.view_tray', tray_id=tray_id))
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
    """
    Remove multiple items from their trays.
    """
    try:
        data = get_json_payload()
        item_type = normalize_optional_text(data.get('item_type'), field_name='Item type', max_length=16)
        unique_ids = normalize_identifier_list(data.get('uniqueIDs', []), field_name='uniqueIDs')
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)})
    if item_type not in {'stock', 'cross'}:
        return jsonify({"success": False, "message": "Invalid item type"})
    
    user = session.get('username')
    success_count = 0
    
    for item_id in unique_ids:
        # Use move_item_to_tray with empty tray_id and position to remove from tray
        if move_item_to_tray(user, item_type, item_id, '', '', db):
            success_count += 1
    
    if success_count > 0:
        write_activity(
            user,
            f"Bulk removed {success_count} {item_type}s from trays",
            db
        )
        message = f"Successfully removed {success_count} out of {len(unique_ids)} items from trays"
        return jsonify({
            "success": True,
            "message": message,
            "results": {
                "success": unique_ids[:success_count],
                "failed": unique_ids[success_count:]
            }
        })
    else:
        return jsonify({
            "success": False,
            "message": "Failed to remove any items from trays"
        })

# API endpoint to get tray occupancy data for the UI
@bp.route('/api/tray/<tray_id>/occupancy')
@login_required
def get_tray_occupancy_api(tray_id):
    """
    Get tray occupancy data in JSON format for the UI.
    """
    
    user = session.get('username')
    
    # Get tray data
    tray = get_tray(user, tray_id, db)
    if not tray:
        return jsonify({"success": False, "message": "Tray not found"})
        
    # Get occupancy data
    occupancy = get_tray_occupancy(user, tray_id, db)
    
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