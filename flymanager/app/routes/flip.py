import os
from datetime import datetime
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    session,
    jsonify,
    url_for,
    current_app,
    flash,
)

# Import necessary components from the app context and services
from flymanager.app import db, MIN_FLIP_DIFFERENCE, socketio
from flymanager.app.services import scanner as scanner_service

# Import utility functions
from flymanager.utils.scanner import get_available_ports
from flymanager.utils.mongo import (
    get_user_stocks,
    get_user_crosses,
    flip_stock,
    flip_cross,
    get_flip_schedule,
    get_user_initials,
    write_activity,
)
from flymanager.utils.labels import generate_label_pdf
from flymanager.app.routes.auth import login_required

# Define the Blueprint
bp = Blueprint(
    "flip", __name__, url_prefix="/flip"
)  # url_prefix defined in app/__init__


@bp.route("/")  # Corresponds to /flip/
@login_required
def flip_interface():
    """Renders the main flipping interface."""
    username = session.get("username")
    try:
        ports = get_available_ports()
    except Exception as e:
        print(f"Error getting available ports: {e}")
        ports = []
        flash("Could not retrieve scanner ports.", "error")

    # Pass active threads to template to show current scanning status?
    # active_scan_info = {tid: info['port_index'] for tid, info in scanner_service.active_threads.items()}
    return render_template(
        "flip.html", username=username, ports=ports
    )  # Add active_scan_info if needed


@bp.route("/start_scan", methods=["POST"])
def start_scan_route():
    """Starts the QR code scanning service on a selected port."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Authentication required."}), 401

    username = session.get("username")
    data = request.json
    port_index = data.get("port_index")

    if port_index is None:
        return jsonify({"success": False, "message": "Port index is required."}), 400

    try:
        port_index = int(port_index)
        # Call the service function from scanner.py
        response, status_code = scanner_service.start_scan_service(port_index, username)
        return response, status_code
    except ValueError:
        return jsonify({"success": False, "message": "Invalid port index."}), 400
    except Exception as e:
        print(f"Error starting scan: {e}")
        return jsonify({"success": False, "message": "Failed to start scanning."}), 500


@bp.route("/stop_scan", methods=["POST"])
def stop_scan_route():
    """Stops a specific QR code scanning thread."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Authentication required."}), 401

    data = request.json
    thread_id = data.get("thread_id")

    if not thread_id:
        return jsonify({"success": False, "message": "Thread ID is required."}), 400

    try:
        # Call the service function from scanner.py
        response, status_code = scanner_service.stop_scan_service(thread_id)
        return response, status_code
    except Exception as e:
        print(f"Error stopping scan: {e}")
        return jsonify({"success": False, "message": "Failed to stop scanning."}), 500


@bp.route("/flip_vial", methods=["POST"])
def handle_flip_vial_route():
    """Handles the logic for flipping a single vial (stock or cross)."""
    if not session.get("username"):
        # Check if request expects JSON or redirect
        if (
            request.accept_mimetypes.accept_json
            and not request.accept_mimetypes.accept_html
        ):
            return jsonify({"message": "Authentication required."}), 401
        else:
            # This route is likely called via JS, so JSON response is more appropriate
            return jsonify({"message": "Authentication required."}), 401

    username = session.get("username")
    data = request.json
    uid = data.get("uniqueID")
    status = data.get("status")  # Optional new status
    flip_time_str = data.get("flipTime")  # Optional specific time
    comment = data.get("comment")  # Optional comment

    if not uid:
        return jsonify({"message": "UniqueID is required."}), 400

    try:
        # Determine flip time
        flip_time = datetime.now()
        if flip_time_str:
            try:
                # Assuming ISO format like YYYY-MM-DDTHH:MM:SS from JS Date.toISOString()
                # Need to handle potential timezone info if present
                flip_time = datetime.fromisoformat(flip_time_str.replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"message": "Invalid flip time format."}), 400

        # Check stocks and crosses
        stock = db["stocks"].find_one({"UniqueID": uid, "User": username})
        cross = db["crosses"].find_one({"UniqueID": uid, "User": username})

        item_type = None
        item_data = None
        flip_function = None
        last_flip_str = None

        if stock:
            item_type = "Stock"
            item_data = stock
            flip_function = flip_stock
            last_flip_str = stock.get("LastFlipDate")
        elif cross:
            item_type = "Cross"
            item_data = cross
            flip_function = flip_cross
            last_flip_str = cross.get("LastFlipDate")
        else:
            return jsonify({"message": "UID not recognized for this user."}), 404

        # Check last flip time to prevent rapid re-flips
        if last_flip_str:
            try:
                # Assuming format 'YYYY-MM-DD HH:MM:SS' or similar from utils/mongo update functions
                # Need to ensure consistency in date format storage
                try:
                    last_flip_dt = datetime.strptime(
                        last_flip_str.split(".")[0], "%Y-%m-%d %H:%M:%S"
                    )
                except:
                    last_flip_dt = datetime.strptime(
                        last_flip_str.split(".")[0], "%Y-%m-%d %H:%M"
                    )
                difference = (flip_time - last_flip_dt).total_seconds()
                if difference < MIN_FLIP_DIFFERENCE:
                    print(
                        f"{item_type} {uid} already flipped recently ({difference:.0f}s ago)"
                    )
                    return (
                        jsonify(
                            {
                                "message": f'{item_type} already flipped recently at: {last_flip_dt.strftime("%Y-%m-%d %H:%M:%S")}'
                            }
                        ),
                        409,
                    )  # 409 Conflict

            except (ValueError, TypeError) as e:
                print(
                    f"Could not parse last flip date '{last_flip_str}' for {item_type.lower()} {uid}: {e}"
                )
                # Decide whether to proceed or return an error if date is unparseable

        print(f"Flipping {item_type.lower()}: {uid} for user {username}")
        # Call the appropriate flip function (flip_stock or flip_cross)
        flip_function(
            username, uid, db, flip_time, new_status=status, added_comment=comment
        )
        write_activity(username, f"Flipped {item_type.lower()} {uid}", db)

        # Emit update via SocketIO? (Optional)
        # socketio.emit('vial_flipped', {'uniqueID': uid, 'type': item_type.lower(), 'status': status or item_data.get('Status')})

        return jsonify({"message": f"{item_type} flipped successfully!"})

    except Exception as e:
        print(f"Error handling flip vial for {uid}: {e}")
        # Log the full traceback for debugging
        import traceback

        traceback.print_exc()
        return jsonify({"message": "An internal error occurred during flip."}), 500


@bp.route("/bulk_flip", methods=["POST"])
def bulk_flip_route():
    """Handles the logic for flipping multiple vials at once."""
    if not session.get("username"):
        return jsonify({"message": "Authentication required."}), 401

    username = session.get("username")
    data = request.json
    uids = data.get("uniqueIDs", [])
    status = data.get("status")  # Optional new status for all items
    flip_time_str = data.get("flipTime")  # Optional specific time
    comment = data.get("comment", "")  # Optional comment

    if not uids:
        return jsonify({"message": "At least one UniqueID is required."}), 400

    if not isinstance(uids, list):
        return jsonify({"message": "uniqueIDs must be a list."}), 400

    try:
        # Determine flip time
        flip_time = datetime.now()
        if flip_time_str:
            try:
                flip_time = datetime.fromisoformat(flip_time_str.replace("Z", "+00:00"))
            except ValueError:
                return jsonify({"message": "Invalid flip time format."}), 400

        results = {"success": [], "failed": []}

        for uid in uids:
            try:
                # Check stocks and crosses
                stock = db["stocks"].find_one({"UniqueID": uid, "User": username})
                cross = db["crosses"].find_one({"UniqueID": uid, "User": username})

                item_type = None
                item_data = None
                flip_function = None

                if stock:
                    item_type = "Stock"
                    item_data = stock
                    flip_function = flip_stock
                elif cross:
                    item_type = "Cross"
                    item_data = cross
                    flip_function = flip_cross
                else:
                    results["failed"].append(
                        {"uid": uid, "reason": "UID not recognized for this user."}
                    )
                    continue

                # Flip the item
                flip_function(
                    username,
                    uid,
                    db,
                    flip_time,
                    new_status=status,
                    added_comment=comment,
                )
                write_activity(
                    username, f"Flipped {item_type.lower()} {uid} (bulk operation)", db
                )
                results["success"].append({"uid": uid, "type": item_type})

            except Exception as e:
                results["failed"].append({"uid": uid, "reason": str(e)})
                print(f"Error flipping {uid}: {e}")

        return jsonify(
            {
                "message": f'Bulk flip completed. {len(results["success"])} successful, {len(results["failed"])} failed.',
                "results": results,
            }
        )

    except Exception as e:
        print(f"Error handling bulk flip: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"message": "An internal error occurred during bulk flip."}), 500


@bp.route("/bulk_status_change", methods=["POST"])
def bulk_status_change_route():
    """Handles the logic for changing the status of multiple vials at once without flipping."""
    if not session.get("username"):
        return jsonify({"message": "Authentication required."}), 401

    username = session.get("username")
    data = request.json
    uids = data.get("uniqueIDs", [])
    status = data.get("status")  # New status for all items

    if not uids:
        return jsonify({"message": "At least one UniqueID is required."}), 400

    if not status:
        return jsonify({"message": "Status is required."}), 400

    if not isinstance(uids, list):
        return jsonify({"message": "uniqueIDs must be a list."}), 400

    try:
        results = {"success": [], "failed": []}

        for uid in uids:
            try:
                # Check stocks and crosses
                stock = db["stocks"].find_one({"UniqueID": uid, "User": username})
                cross = db["crosses"].find_one({"UniqueID": uid, "User": username})

                if stock:
                    db["stocks"].update_one(
                        {"UniqueID": uid, "User": username},
                        {
                            "$set": {
                                "Status": status,
                                "DataModifiedDate": datetime.now().strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            }
                        },
                    )
                    item_type = "Stock"
                elif cross:
                    db["crosses"].update_one(
                        {"UniqueID": uid, "User": username},
                        {
                            "$set": {
                                "Status": status,
                                "DataModifiedDate": datetime.now().strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            }
                        },
                    )
                    item_type = "Cross"
                else:
                    results["failed"].append(
                        {"uid": uid, "reason": "UID not recognized for this user."}
                    )
                    continue

                write_activity(
                    username,
                    f"Changed status of {item_type.lower()} {uid} to {status} (bulk operation)",
                    db,
                )
                results["success"].append({"uid": uid, "type": item_type})

            except Exception as e:
                results["failed"].append({"uid": uid, "reason": str(e)})
                print(f"Error changing status for {uid}: {e}")

        return jsonify(
            {
                "message": f'Bulk status change completed. {len(results["success"])} successful, {len(results["failed"])} failed.',
                "results": results,
            }
        )

    except Exception as e:
        print(f"Error handling bulk status change: {e}")
        import traceback

        traceback.print_exc()
        return (
            jsonify(
                {"message": "An internal error occurred during bulk status change."}
            ),
            500,
        )


@bp.route("/schedule")
@login_required
def flip_schedule_display():
    """Displays the upcoming flip schedule for the user."""
    username = session.get("username")
    try:
        schedule = get_flip_schedule(
            username, db
        )  # Fetch raw schedule {date: [item_str, ...]}

        enhanced_schedule = {}
        # Fetch all stocks and crosses once for efficient lookup
        stocks = get_user_stocks(username, db)
        crosses = get_user_crosses(username, db)
        stock_map = {s.get("UniqueID"): s for s in stocks}
        cross_map = {c.get("UniqueID"): c for c in crosses}

        for date_str, items in sorted(schedule.items()):  # Sort by date
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                day_of_week = date_obj.strftime("%A")
            except ValueError:
                print(
                    f"Warning: Invalid date format '{date_str}' in schedule for user {username}"
                )
                continue  # Skip invalid date entries

            trays_needed = set()
            processed_items = []

            for item_str in items:
                uid = None
                item_type = None
                item_data = None
                tray_id = None
                # Parse UID and determine type from string like "Flip Stock (ID: XXX, ...)"
                try:
                    # More robust parsing needed if format varies
                    id_part_start = item_str.find("(ID: ")
                    if id_part_start != -1:
                        id_part_end = item_str.find(",", id_part_start)
                        if id_part_end == -1:
                            id_part_end = item_str.find(
                                ")", id_part_start
                            )  # Handle case with no comma after ID
                        if id_part_end != -1:
                            uid = item_str[id_part_start + 5 : id_part_end].strip()

                    if uid:
                        if "Stock" in item_str:
                            item_type = "stock"
                            item_data = stock_map.get(uid)
                        elif "Cross" in item_str:
                            item_type = "cross"
                            item_data = cross_map.get(uid)

                        if item_data:
                            tray_id = item_data.get("TrayID")
                            if tray_id:
                                trays_needed.add(str(tray_id))
                        else:
                            print(
                                f"Warning: Data not found for scheduled {item_type or 'item'} UID {uid}"
                            )

                    # Add processed item info
                    processed_items.append(
                        {
                            "display_string": item_str,
                            "uid": uid,
                            "type": item_type,
                            "tray": tray_id,
                            "data": item_data,  # Include full data if needed by template
                        }
                    )
                except Exception as parse_err:
                    print(
                        f"Error parsing schedule item string '{item_str}': {parse_err}"
                    )
                    # Add item even if parsing fails partially
                    processed_items.append(
                        {
                            "display_string": item_str,
                            "uid": None,
                            "type": None,
                            "tray": None,
                            "data": None,
                        }
                    )

            enhanced_schedule[date_str] = {
                "flip_items": processed_items,  # Use processed list
                "day_of_week": day_of_week,
                "trays": sorted(list(trays_needed)),
            }

        return render_template(
            "flip_schedule.html", schedule=enhanced_schedule, username=username
        )

    except Exception as e:
        print(f"Error generating flip schedule display for {username}: {e}")
        import traceback

        traceback.print_exc()
        flash("Error generating flip schedule.", "error")
        # Render template with empty schedule
        return render_template(
            "flip_schedule.html",
            schedule={},
            username=username,
            error="Could not generate schedule.",
        )


@bp.route("/generate_labels_for_day", methods=["POST"])
@login_required
def generate_labels_for_day_route():
    """Generates labels for all items scheduled to be flipped on a specific day."""
    username = session.get("username")
    try:
        date_str = request.form.get("date")
        blank_spaces = int(request.form.get("blank_spaces", 0))

        if not date_str:
            flash("Date is required.", "error")
            return redirect(url_for("flip.flip_schedule_display"))

        # Validate date format
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            flash("Invalid date format. Please use YYYY-MM-DD.", "error")
            return redirect(url_for("flip.flip_schedule_display"))

        # Fetch schedule items for the specific date
        schedule = get_flip_schedule(username, db)
        items_for_date = schedule.get(date_str, [])

        if not items_for_date:
            flash(f"No items scheduled for {date_str}.", "info")
            return redirect(url_for("flip.flip_schedule_display"))

        selected_items_data = []
        item_types = []
        # Fetch all stocks and crosses once
        stocks = get_user_stocks(username, db)
        crosses = get_user_crosses(username, db)
        stock_map = {s.get("UniqueID"): s for s in stocks}
        cross_map = {c.get("UniqueID"): c for c in crosses}

        for item_str in items_for_date:
            # Parse UID and type from string (similar to schedule display)
            uid = None
            item_type = None
            item_data = None
            try:
                id_part_start = item_str.find("(ID: ")
                if id_part_start != -1:
                    id_part_end = item_str.find(",", id_part_start)
                    if id_part_end == -1:
                        id_part_end = item_str.find(")", id_part_start)
                    if id_part_end != -1:
                        uid = item_str[id_part_start + 5 : id_part_end].strip()

                if uid:
                    if "Stock" in item_str:
                        item_type = "stock"
                        item_data = stock_map.get(uid)
                    elif "Cross" in item_str:
                        item_type = "cross"
                        item_data = cross_map.get(uid)

                    if item_data:
                        selected_items_data.append(item_data)
                        item_types.append(item_type)
                    else:
                        print(
                            f"Warning: Data not found for scheduled {item_type or 'item'} UID {uid} during label generation."
                        )
                else:
                    print(
                        f"Could not extract UID from schedule item string: {item_str}"
                    )

            except Exception as parse_err:
                print(
                    f"Error parsing schedule item string '{item_str}' for labels: {parse_err}"
                )
                continue  # Skip unparseable items

        if not selected_items_data:
            flash(f"Could not find data for items scheduled on {date_str}.", "warning")
            return redirect(url_for("flip.flip_schedule_display"))

        # Sort items by tray/position before generating labels
        # Need a combined list to sort properly if mixing stocks/crosses
        # Assuming generate_label_pdf can handle a list of dicts with varying keys
        # Let's sort based on TrayID and TrayPosition present in both stock/cross dicts
        def sort_key(item):
            tray_id = str(item.get("TrayID", ""))
            try:
                # Handle potential non-integer TrayPosition like '1.0'
                pos = int(float(item.get("TrayPosition", "0") or "0"))
            except ValueError:
                pos = 0  # Default position if conversion fails
            return (tray_id, pos)

        # Create pairs of (data, type), sort by data, then extract sorted lists
        paired_list = list(zip(selected_items_data, item_types))
        paired_list.sort(key=lambda pair: sort_key(pair[0]))
        sorted_items_data, sorted_item_types = (
            zip(*paired_list) if paired_list else ([], [])
        )

        user_initials = get_user_initials(username, db)

        # Generate PDF
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = f"{username}_labels_{date_str.replace('-', '')}_{timestamp}"  # Use date in filename
        pdf_filename = f"{filename_base}.pdf"
        labels_dir = os.path.join(current_app.static_folder, "generated_labels")
        os.makedirs(labels_dir, exist_ok=True)
        pdf_full_path = os.path.join(labels_dir, pdf_filename)

        print(f"Generating PDF: {pdf_full_path} with {len(sorted_items_data)} items.")
        try:
            generate_label_pdf(
                pdf_full_path,
                user_initials,
                list(sorted_items_data),  # Pass as list
                list(sorted_item_types),  # Pass as list
                blank_spaces,
                len(sorted_items_data),
            )
        except Exception as pdf_err:
            print(f"Error during PDF generation: {pdf_err}")
            import traceback

            traceback.print_exc()
            flash(f"Error occurred during PDF generation: {pdf_err}", "error")
            return redirect(url_for("flip.flip_schedule_display"))

        pdf_url = url_for("static", filename=f"generated_labels/{pdf_filename}")

        # Log activity
        write_activity(
            username, f"Generated {len(sorted_items_data)} labels for {date_str}", db
        )

        # Return redirect to the generated PDF
        return redirect(pdf_url)

    except ValueError as ve:  # Catch potential int conversion errors
        flash(f"Invalid input: {ve}", "error")
        return redirect(url_for("flip.flip_schedule_display"))
    except Exception as e:
        print(f"Error generating labels for day {date_str}: {e}")
        import traceback

        traceback.print_exc()
        flash(f"Error generating labels for day: {e}", "error")
        return redirect(url_for("flip.flip_schedule_display"))
