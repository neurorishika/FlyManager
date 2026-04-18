import threading
from datetime import datetime  # Added import

import serial
from flask import current_app, jsonify

from flymanager.app import active_threads, db, socketio
# Import get_available_ports from its utility location
from flymanager.utils.mongo.access import (get_accessible_cross,
                                           get_accessible_stock)
from flymanager.utils.scanner import get_available_ports

# Note: MIN_FLIP_DIFFERENCE is defined in app/__init__.py


def _build_stock_payload(stock):
    return {
        'uniqueID': stock.get('UniqueID', ''),
        'name': stock.get('Name', ''),
        'genotype': stock.get('Genotype', ''),
        'status': stock.get('Status', ''),
        'seriesID': stock.get('SeriesID', ''),
        'replicateID': stock.get('ReplicateID', ''),
        'trayID': stock.get('TrayID', ''),
        'trayPosition': stock.get('TrayPosition', ''),
        'foodType': stock.get('FoodType', ''),
        'provenance': stock.get('Provenance', ''),
        'altReference': stock.get('AltReference', ''),
    }


def _build_cross_payload(cross):
    return {
        'uniqueID': cross.get('UniqueID', ''),
        'name': cross.get('Name', ''),
        'status': cross.get('Status', ''),
        'maleGenotype': cross.get('MaleGenotype', ''),
        'femaleGenotype': cross.get('FemaleGenotype', ''),
        'trayID': cross.get('TrayID', ''),
        'trayPosition': cross.get('TrayPosition', ''),
        'foodType': cross.get('FoodType', ''),
    }


def lookup_uid_result(username, uid, database=None):
    """Resolve a scanned UID to a stock or cross payload for the current user."""
    active_db = database or db
    stock = get_accessible_stock(username, uid, active_db)
    if stock:
        return 'stock', _build_stock_payload(stock)

    cross = get_accessible_cross(username, uid, active_db)
    if cross:
        return 'cross', _build_cross_payload(cross)

    return None, None

def scan_qr_code_thread(port_index, ports, username, thread_id, baudrate=9600, size=10):
    """
    Thread function to listen for QR codes on a serial port.
    """
    port_device = ports[port_index].device
    try:
        # Use a context manager for the serial port if possible, or ensure close() is called
        port = serial.Serial(port_device, baudrate, timeout=1)
        print(f"Thread {thread_id}: Started listening on {port_device}")
    except serial.SerialException as e:
        print(f"Thread {thread_id}: Error opening serial port {port_device}: {e}")
        # Ensure socketio emits happen in a context where it's safe (usually okay)
        socketio.emit('scan_error', {'message': f"Error opening port {port_device}: {e}"})
        if thread_id in active_threads:
             # Ensure thread-safe access if multiple threads could modify active_threads
             # For simple add/del, dicts are mostly safe, but use locks if complex ops needed
             del active_threads[thread_id]
        return

    keystrokes = []

    try: # Wrap the main loop in try/finally to ensure port closure
        while True:
            # Check for stop signal (thread-safe access to active_threads assumed)
            if thread_id not in active_threads or not active_threads[thread_id]['running']:
                print(f'Thread {thread_id}: Stopping signal received.')
                break

            try:
                data_bytes = port.read() # Read bytes
                if not data_bytes: # Timeout occurred
                    continue

                data = data_bytes.decode("utf-8", errors='ignore')

                if data == "\r":
                    qr_code = "".join(keystrokes).strip()
                    print(f"Thread {thread_id}: Received potential code: '{qr_code}' (Length: {len(qr_code)})")
                    if len(qr_code) == size:
                        print(f'Thread {thread_id}: Scanned QR code: {qr_code}')
                        uid = qr_code

                        # --- Database Lookup ---
                        # WARNING: Direct DB access from background threads can be complex.
                        # Ensure your DB driver/library (PyMongo) handles connections safely
                        # across threads (connection pooling is common).
                        # A potentially safer pattern is to emit the UID via socketio
                        # and handle the DB lookup in a socketio event handler in the main process.
                        try:
                            item_type, payload = lookup_uid_result(username, uid, db)

                            # Emit results via SocketIO (generally thread-safe)
                            if item_type == 'stock':
                                print(f'Thread {thread_id}: Stock scanned: {uid}')
                                socketio.emit('stock_scanned', payload)
                            elif item_type == 'cross':
                                print(f'Thread {thread_id}: Cross scanned: {uid}')
                                socketio.emit('cross_scanned', payload)
                            else:
                                print(f'Thread {thread_id}: QR code {uid} not recognized.')
                                socketio.emit('qr_not_recognized', {'uniqueID': uid})

                        except Exception as db_err:
                             print(f"Thread {thread_id}: Database error processing UID {uid}: {db_err}")
                             socketio.emit('scan_error', {'message': f"Database error processing UID {uid}"})

                    else:
                         if qr_code:
                              print(f"Thread {thread_id}: Ignoring code of incorrect length ({len(qr_code)}): '{qr_code}'")

                    keystrokes = []
                elif data.isprintable():
                    keystrokes.append(data)

            except serial.SerialException as ser_err:
                print(f"Thread {thread_id}: Serial error on {port_device}: {ser_err}")
                socketio.emit('scan_error', {'message': f"Serial error on port {port_device}: {ser_err}"})
                break # Exit loop on serial error
            except Exception as e:
                print(f"Thread {thread_id}: Unexpected error in loop: {e}")
                import traceback
                traceback.print_exc()
                # Decide whether to continue or break on unexpected errors

    finally:
        # --- Cleanup ---
        if port and port.is_open:
            port.close()
            print(f"Thread {thread_id}: Closed serial port {port_device}")

        # Remove thread from active_threads dictionary upon exit
        if thread_id in active_threads:
            # Consider using a lock if active_threads access needs more robust synchronization
            del active_threads[thread_id]
            print(f"Thread {thread_id}: Removed from active threads.")


def start_scan_service(port_index, username):
    """Starts the QR code scanning thread."""
    try:
        ports = get_available_ports()
        if not isinstance(port_index, int) or port_index < 0 or port_index >= len(ports):
             return jsonify({'success': False, 'message': 'Invalid port index.'}), 400

        # Use a more robust unique ID if needed, timestamp is usually sufficient for transient threads
        thread_id = f"scan_{datetime.now().timestamp()}"

        # Optional: Check if a thread is already running for this port_index

        scan_thread = threading.Thread(
            target=scan_qr_code_thread,
            args=(port_index, ports, username, thread_id),
            daemon=True # Daemon threads exit when the main program exits
        )
        # Ensure thread-safe access if needed
        active_threads[thread_id] = {'thread': scan_thread, 'running': True, 'port_index': port_index}
        scan_thread.start()
        print(f'Started scanning thread: {thread_id} for user {username} on port index {port_index}')

        return jsonify({'success': True, 'message': 'Started scanning', 'thread_id': thread_id}), 200

    except Exception as e:
        print(f"Error starting scan service: {e}")
        return jsonify({'success': False, 'message': 'Failed to start scanning service.'}), 500


def stop_scan_service(thread_id):
    """Signals a specific QR code scanning thread to stop."""
    if thread_id in active_threads:
        print(f"Attempting to stop scanning thread: {thread_id}")
        # Signal the thread to stop (thread-safe access assumed)
        active_threads[thread_id]['running'] = False

        # Don't join the thread here in a request context, it can block.
        # The thread is responsible for cleaning itself up from active_threads.
        return jsonify({'success': True, 'message': 'Stop signal sent to scanning thread.'}), 200
    else:
        print(f"Stop request for non-existent or already stopped thread ID: {thread_id}")
        return jsonify({'success': False, 'message': 'Scanning thread not found or already stopped.'}), 404
        print(f"Stop request for non-existent or already stopped thread ID: {thread_id}")
        return jsonify({'success': False, 'message': 'Scanning thread not found or already stopped.'}), 404
        return jsonify({'success': False, 'message': 'Scanning thread not found or already stopped.'}), 404
