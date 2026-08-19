import os
from datetime import datetime

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

# Import necessary components from the app context and services
from flymanager.app import MIN_FLIP_DIFFERENCE, db, socketio
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.routes.auth import login_required
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_identifier_list,
                                     normalize_optional_text, parse_int_value,
                                     parse_iso_datetime)
from flymanager.app.services import scanner as scanner_service
from flymanager.utils.labels import generate_label_pdf
from flymanager.utils.mongo import (OperationLockConflict, edit_cross,
                                    edit_stock, flip_cross, flip_stock,
                                    get_accessible_cross, get_accessible_stock,
                                    get_flip_schedule,
                                    get_maintainable_crosses,
                                    get_maintainable_stocks, get_user_initials,
                                    hold_operation_lock, hold_operation_locks,
                                    record_operation_lock_keys, write_activity)
from flymanager.utils.mongo_records import seconds_since_last_flip
# Import utility functions
from flymanager.utils.scanner import get_available_ports

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
        "flip.html",
        username=username,
        ports=ports,
        enable_client_serial_scanner=current_app.config.get(
            "ENABLE_CLIENT_SERIAL_SCANNER", False
        ),
        enable_camera_scanner=current_app.config.get(
            "ENABLE_CAMERA_SCANNER", True
        ),
    )  # Add active_scan_info if needed


@bp.route("/start_scan", methods=["POST"])
@limiter.limit("20 per minute")
def start_scan_route():
    """Starts the QR code scanning service on a selected port."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Authentication required."}), 401

    username = session.get("username")
    try:
        data = get_json_payload()
        port_index = parse_int_value(
            data.get("port_index"), field_name="Port index", minimum=0, maximum=255
        )
        # Call the service function from scanner.py
        response, status_code = scanner_service.start_scan_service(port_index, username)
        return response, status_code
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as e:
        current_app.logger.exception("Error starting scan for %s: %s", username, e)
        return jsonify({"success": False, "message": "Failed to start scanning."}), 500


@bp.route("/stop_scan", methods=["POST"])
@limiter.limit("20 per minute")
def stop_scan_route():
    """Stops a specific QR code scanning thread."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Authentication required."}), 401

    try:
        data = get_json_payload()
        thread_id = normalize_optional_text(
            data.get("thread_id"), field_name="Thread ID", max_length=128
        )
        if not thread_id:
            return jsonify({"success": False, "message": "Thread ID is required."}), 400
        # Call the service function from scanner.py
        response, status_code = scanner_service.stop_scan_service(thread_id)
        return response, status_code
    except Exception as e:
        current_app.logger.exception("Error stopping scan thread %s: %s", thread_id, e)
        return jsonify({"success": False, "message": "Failed to stop scanning."}), 500


@bp.route("/lookup_uid", methods=["POST"])
@limiter.limit("120 per minute")
def lookup_uid_route():
    """Resolve a scanned UID for browser-driven scanner workflows."""
    if not session.get("username"):
        return jsonify({"success": False, "message": "Authentication required."}), 401

    username = session.get("username")
    try:
        data = get_json_payload()
        uid = normalize_optional_text(data.get("uniqueID"), field_name="UniqueID", max_length=64)
        if not uid:
            return jsonify({"success": False, "message": "UniqueID is required."}), 400

        item_type, payload = scanner_service.lookup_uid_result(username, uid, db)
        if not payload:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "UID not recognized for this user.",
                        "uniqueID": uid,
                    }
                ),
                404,
            )

        return jsonify({"success": True, "itemType": item_type, "payload": payload})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as e:
        current_app.logger.exception("Error looking up scanned UID %s for %s: %s", uid, username, e)
        return jsonify({"success": False, "message": "Failed to look up UID."}), 500


@bp.route("/flip_vial", methods=["POST"])
@limiter.limit("60 per minute")
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
    try:
        data = get_json_payload()
        uid = normalize_optional_text(data.get("uniqueID"), field_name="UniqueID", max_length=64)
        status = normalize_optional_text(data.get("status"), field_name="Status", max_length=64)
        comment = normalize_optional_text(data.get("comment"), field_name="Comment", max_length=500)
        if not uid:
            return jsonify({"message": "UniqueID is required."}), 400

        # Determine flip time
        flip_time = parse_iso_datetime(data.get("flipTime"), field_name="flip time")

        # Check stocks and crosses
        stock = get_accessible_stock(username, uid, db)
        cross = get_accessible_cross(username, uid, db)

        item_type = None
        item_data = None
        flip_function = None
        last_flip_str = None

        if stock:
            item_type = "Stock"
            item_data = stock
            flip_function = flip_stock
            last_flip_str = stock.get("LastFlipDate")
            owner_username = stock.get("User", username)
        elif cross:
            item_type = "Cross"
            item_data = cross
            flip_function = flip_cross
            last_flip_str = cross.get("LastFlipDate")
            owner_username = cross.get("User", username)
        else:
            return jsonify({"message": "UID not recognized for this user."}), 404

        # Check last flip time to prevent rapid re-flips. Shared with the
        # bulk-flip path via seconds_since_last_flip so this rule can't
        # silently drift out of sync between the two again (see
        # bulk_operations.py's bulk_flip_records for the other call site).
        difference = seconds_since_last_flip(last_flip_str, flip_time)
        if difference is not None and difference < MIN_FLIP_DIFFERENCE:
            current_app.logger.info(
                "%s %s already flipped recently (%.0fs ago)",
                item_type,
                uid,
                difference,
            )
            return (
                jsonify(
                    {
                        "message": f"{item_type} already flipped recently at: {last_flip_str}"
                    }
                ),
                409,
            )  # 409 Conflict
                # Decide whether to proceed or return an error if date is unparseable

        current_app.logger.info("Flipping %s %s for user %s", item_type.lower(), uid, username)
        with hold_operation_lock(
            db,
            key=f"record-mutation:{uid}",
            actor=username,
            label=f"Flip {item_type.lower()} {uid}",
            ttl_seconds=300,
            metadata={"route": "flip_vial", "uid": uid},
            conflict_message=f"{item_type} {uid} is already being updated. Please wait for that request to finish.",
        ):
            flip_function(
                owner_username, uid, db, flip_time, new_status=status, added_comment=comment
            )
            write_activity(username, f"Flipped {item_type.lower()} {uid}", db)

        # Emit update via SocketIO? (Optional)
        # socketio.emit('vial_flipped', {'uniqueID': uid, 'type': item_type.lower(), 'status': status or item_data.get('Status')})

        return jsonify({"message": f"{item_type} flipped successfully!"})

    except OperationLockConflict as exc:
        return jsonify({"message": str(exc)}), 409
    except Exception as e:
        current_app.logger.exception("Error handling flip vial for %s: %s", uid, e)
        return jsonify({"message": "An internal error occurred during flip."}), 500


@bp.route("/bulk_flip", methods=["POST"])
@limiter.limit("20 per minute")
def bulk_flip_route():
    """Queues a background job that flips the selected vials.

    The heavy lifting (batched reads + writes across potentially hundreds of
    records) runs in the RQ worker instead of blocking the request, so large
    selections no longer time out. The jobs banner polls /jobs/status.json and
    toasts when the flip finishes.
    """
    if not session.get("username"):
        return jsonify({"message": "Authentication required."}), 401

    username = session.get("username")
    try:
        data = get_json_payload()
        uids = normalize_identifier_list(data.get("uniqueIDs", []), field_name="uniqueIDs")
        status = normalize_optional_text(data.get("status"), field_name="Status", max_length=64)
        comment = normalize_optional_text(data.get("comment"), field_name="Comment", max_length=500) or ""

        # Validate/normalise the flip time here so the user gets an immediate
        # error rather than a failed background job.
        flip_time = parse_iso_datetime(data.get("flipTime"), field_name="flip time")

        if not uids:
            return jsonify({"message": "No items selected for flipping."}), 400

        key = f"bulk-flip:user:{username}"
        enqueue_job(
            db,
            key=key,
            actor=username,
            label=f"Bulk flip ({len(uids)} items)",
            func=job_tasks.task_bulk_flip,
            kwargs={
                "key": key,
                "username": username,
                "uids": uids,
                "flip_time": flip_time.isoformat(),
                "status": status,
                "comment": comment,
            },
            ttl_seconds=1800,
            metadata={"route": "bulk_flip", "uid_count": len(uids)},
            conflict_message="A bulk flip for you is already running. Please wait for it to finish before starting another.",
        )
    except OperationLockConflict as exc:
        return jsonify({"message": str(exc), "queued": False}), 409
    except ValueError as exc:
        return jsonify({"message": str(exc), "queued": False}), 400
    except Exception as e:
        current_app.logger.exception("Error queuing bulk flip for %s: %s", username, e)
        return jsonify({"message": "An internal error occurred while queuing the bulk flip."}), 500

    return (
        jsonify(
            {
                "queued": True,
                "job": key,
                "message": f"Flipping {len(uids)} item{'s' if len(uids) != 1 else ''} in the background. Watch the jobs banner for progress.",
            }
        ),
        202,
    )


@bp.route("/bulk_status_change", methods=["POST"])
@limiter.limit("20 per minute")
def bulk_status_change_route():
    """Queues a background job that changes the status of the selected records."""
    if not session.get("username"):
        return jsonify({"message": "Authentication required."}), 401

    username = session.get("username")
    try:
        data = get_json_payload()
        uids = normalize_identifier_list(data.get("uniqueIDs", []), field_name="uniqueIDs")
        status = normalize_optional_text(data.get("status"), field_name="Status", max_length=64)
        comment = normalize_optional_text(data.get("comment"), field_name="Comment", max_length=500)
        if not status:
            return jsonify({"message": "Status is required."}), 400

        if not uids:
            return jsonify({"message": "No items selected."}), 400

        key = f"bulk-status:user:{username}"
        enqueue_job(
            db,
            key=key,
            actor=username,
            label=f"Bulk status change ({len(uids)} items)",
            func=job_tasks.task_bulk_status_change,
            kwargs={
                "key": key,
                "username": username,
                "uids": uids,
                "status": status,
                "comment": comment,
            },
            ttl_seconds=1800,
            metadata={"route": "bulk_status_change", "uid_count": len(uids), "status": status},
            conflict_message="A bulk status change for you is already running. Please wait for it to finish before starting another.",
        )
    except OperationLockConflict as exc:
        return jsonify({"message": str(exc), "queued": False}), 409
    except ValueError as exc:
        return jsonify({"message": str(exc), "queued": False}), 400
    except Exception as e:
        current_app.logger.exception("Error queuing bulk status change for %s: %s", username, e)
        return (
            jsonify(
                {"message": "An internal error occurred while queuing the bulk status change."}
            ),
            500,
        )

    return (
        jsonify(
            {
                "queued": True,
                "job": key,
                "message": f"Updating {len(uids)} item{'s' if len(uids) != 1 else ''} to {status} in the background. Watch the jobs banner for progress.",
            }
        ),
        202,
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
        stocks = get_maintainable_stocks(username, db)
        crosses = get_maintainable_crosses(username, db)
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
@limiter.limit("10 per hour")
def generate_labels_for_day_route():
    """Generates labels for all items scheduled to be flipped on a specific day."""
    username = session.get("username")
    try:
        date_str = request.form.get("date")
        blank_spaces = parse_int_value(
            request.form.get("blank_spaces", 0),
            field_name="Blank spaces",
            minimum=0,
            maximum=200,
        )

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
        stocks = get_maintainable_stocks(username, db)
        crosses = get_maintainable_crosses(username, db)
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
