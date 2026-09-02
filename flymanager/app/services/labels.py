"""Build flip-label PDFs from a user's schedule.

This logic used to live inline in the /flip/generate_labels_for_day route,
which put it out of reach of the reminder email: the scheduler runs with no
request context, so anything depending on `session` or `url_for` is
unusable there. The helper below takes the username and db explicitly and
returns bytes, so both the route and the email service can call it.
"""

import os
import tempfile
import uuid

from flymanager.app import db
from flymanager.utils.labels import generate_label_pdf
from flymanager.utils.mongo import (get_flip_schedule, get_maintainable_crosses,
                                    get_maintainable_stocks, get_user_initials)


def parse_schedule_item(item_str):
    """Pull the (uid, item_type) pair out of a schedule entry.

    Entries are built by get_flip_schedule as
        "Stock: <link> (ID: <uid>, <tray> - <pos>[, owner: <user>])"
    Returns (None, None) for anything that does not parse, so a single
    malformed entry cannot sink a whole print run.
    """
    id_part_start = item_str.find("(ID: ")
    if id_part_start == -1:
        return None, None

    id_part_end = item_str.find(",", id_part_start)
    if id_part_end == -1:
        id_part_end = item_str.find(")", id_part_start)
    if id_part_end == -1:
        return None, None

    uid = item_str[id_part_start + 5 : id_part_end].strip()
    if not uid:
        return None, None

    # The prefix is what get_flip_schedule wrote, before the record's own
    # name, so a record called "Stock ..." cannot flip the type.
    if item_str.startswith("Stock:"):
        return uid, "stock"
    if item_str.startswith("Cross:"):
        return uid, "cross"
    return None, None


def collect_scheduled_items(username, dates, db):
    """Resolve the schedule entries for `dates` into record dicts.

    Returns (items, types) sorted by (TrayID, TrayPosition) across every
    requested date, so a merged today+overdue run prints in tray order
    rather than date order.
    """
    schedule = get_flip_schedule(username, db)

    stocks = get_maintainable_stocks(username, db)
    crosses = get_maintainable_crosses(username, db)
    stock_map = {s.get("UniqueID"): s for s in stocks}
    cross_map = {c.get("UniqueID"): c for c in crosses}

    selected_items_data = []
    item_types = []
    seen = set()

    for date_str in dates:
        for item_str in schedule.get(date_str, []):
            uid, item_type = parse_schedule_item(item_str)
            if not uid:
                print(f"Could not extract UID from schedule item string: {item_str}")
                continue

            # A record scheduled on two of the requested dates gets one label,
            # not two -- it is one vial being flipped.
            if (uid, item_type) in seen:
                continue

            item_data = (stock_map if item_type == "stock" else cross_map).get(uid)
            if not item_data:
                print(
                    f"Warning: Data not found for scheduled {item_type} UID {uid} "
                    "during label generation."
                )
                continue

            seen.add((uid, item_type))
            selected_items_data.append(item_data)
            item_types.append(item_type)

    def sort_key(item):
        tray_id = str(item.get("TrayID", ""))
        try:
            # Handle potential non-integer TrayPosition like '1.0'
            pos = int(float(item.get("TrayPosition", "0") or "0"))
        except ValueError:
            pos = 0  # Default position if conversion fails
        return (tray_id, pos)

    paired_list = list(zip(selected_items_data, item_types))
    paired_list.sort(key=lambda pair: sort_key(pair[0]))
    sorted_items, sorted_types = zip(*paired_list) if paired_list else ([], [])
    return list(sorted_items), list(sorted_types)


def build_label_pdf(username, dates, db=db, blank_spaces=0):
    """Render labels for everything `username` must flip on `dates`.

    Returns (pdf_bytes, item_count). When nothing is scheduled, returns
    (None, 0) so callers can skip the attachment without catching an
    exception.
    """
    items, types = collect_scheduled_items(username, dates, db)
    if not items:
        return None, 0

    user_initials = get_user_initials(username, db)

    # A temp file, not static/generated_labels: the email attaches bytes and
    # needs no URL, and nothing prunes that directory.
    tmp_path = os.path.join(
        tempfile.gettempdir(), f"flip_labels_{uuid.uuid4().hex}.pdf"
    )
    try:
        generate_label_pdf(
            tmp_path,
            user_initials,
            items,
            types,
            blank_spaces,
            len(items),
        )
        with open(tmp_path, "rb") as handle:
            return handle.read(), len(items)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
