import math

from flask import redirect, request, session, url_for

from flymanager.utils.utils import parse_flip_day

EXPLORER_PER_PAGE_OPTIONS = (
    ("20", "20"),
    ("50", "50"),
    ("100", "100"),
    ("all", "All"),
)


def collect_unique_values(records, field_getters):
    unique_values = {}

    for field_name, getter in field_getters.items():
        unique_values[field_name] = sorted(
            {
                str(value)
                for record in records
                for value in [getter(record)]
                if value not in (None, "")
            }
        )

    return unique_values


def get_explorer_filter_state(*, session_key, clear_endpoint, field_names):
    if request.method == "POST" and "clear_filters" in request.form:
        session.pop(session_key, None)
        return None, redirect(url_for(clear_endpoint))

    if request.method == "POST":
        filter_state = {field_name: request.form.get(field_name, "") for field_name in field_names}
        session[session_key] = filter_state
        return filter_state, None

    return session.get(session_key, {}), None


def normalize_explorer_per_page(value, *, default="20"):
    normalized_value = str(value or "").strip().lower()
    allowed_values = {option_value for option_value, _ in EXPLORER_PER_PAGE_OPTIONS}
    if normalized_value in allowed_values:
        return normalized_value
    return default


def get_explorer_pagination_state(*, session_key, default_per_page="20"):
    stored_state = session.get(session_key, {})
    per_page_value = normalize_explorer_per_page(
        request.values.get("per_page", stored_state.get("per_page", default_per_page)),
        default=default_per_page,
    )
    session[session_key] = {"per_page": per_page_value}

    if request.method == "POST":
        page = 1
    else:
        try:
            page = max(1, int(request.args.get("page", "1")))
        except (TypeError, ValueError):
            page = 1

    return {
        "page": page,
        "per_page": None if per_page_value == "all" else int(per_page_value),
        "per_page_value": per_page_value,
        "per_page_options": [
            {
                "value": option_value,
                "label": option_label,
                "selected": option_value == per_page_value,
            }
            for option_value, option_label in EXPLORER_PER_PAGE_OPTIONS
        ],
    }


def _build_page_display(total_pages, current_page):
    """Build a compact pagination display: 1,2,3 ... current ... N-2,N-1,N.

    The first three and last three pages are always shown; the current page
    is inserted in between (with an ellipsis on each side) when it does not
    already fall within those edge groups.
    """
    pages = set(range(1, min(3, total_pages) + 1))
    pages.update(range(max(1, total_pages - 2), total_pages + 1))
    pages.add(current_page)

    display = []
    previous_page = None
    for page_number in sorted(pages):
        if previous_page is not None and page_number - previous_page > 1:
            display.append({"type": "ellipsis"})
        display.append({"type": "page", "value": page_number})
        previous_page = page_number
    return display


def paginate_explorer_records(records, *, page, per_page, per_page_value):
    total_items = len(records)

    if per_page is None:
        items = list(records)
        return {
            "items": items,
            "page": 1,
            "page_count": len(items),
            "per_page": None,
            "per_page_value": per_page_value,
            "per_page_options": [],
            "total_items": total_items,
            "total_pages": 1,
            "start_index": 1 if total_items else 0,
            "end_index": total_items,
            "has_previous": False,
            "has_next": False,
            "previous_page": None,
            "next_page": None,
            "page_numbers": [{"type": "page", "value": 1}],
            "is_all": True,
        }

    total_pages = max(1, math.ceil(total_items / per_page))
    current_page = min(max(1, page), total_pages)
    start_offset = (current_page - 1) * per_page
    end_offset = start_offset + per_page
    items = list(records[start_offset:end_offset])
    start_index = start_offset + 1 if total_items else 0
    end_index = start_offset + len(items)
    return {
        "items": items,
        "page": current_page,
        "page_count": len(items),
        "per_page": per_page,
        "per_page_value": per_page_value,
        "per_page_options": [],
        "total_items": total_items,
        "total_pages": total_pages,
        "start_index": start_index,
        "end_index": end_index,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
        "previous_page": current_page - 1 if current_page > 1 else None,
        "next_page": current_page + 1 if current_page < total_pages else None,
        "page_numbers": _build_page_display(total_pages, current_page),
        "is_all": False,
    }


def build_pagination_from_db_page(items, total_count, pagination_state):
    """Build the same pagination dict shape as paginate_explorer_records,
    but from a page that MongoDB already sliced via skip/limit - total_count
    comes from count_documents, not len(all_records).
    """
    per_page = pagination_state["per_page"]
    if per_page is None:
        return {
            "items": items,
            "page": 1,
            "page_count": len(items),
            "per_page": None,
            "per_page_value": pagination_state["per_page_value"],
            "total_items": total_count,
            "total_pages": 1,
            "start_index": 1 if total_count else 0,
            "end_index": total_count,
            "has_previous": False,
            "has_next": False,
            "previous_page": None,
            "next_page": None,
            "page_numbers": [{"type": "page", "value": 1}],
            "is_all": True,
        }

    total_pages = max(1, math.ceil(total_count / per_page))
    current_page = min(max(1, pagination_state["page"]), total_pages)
    start_offset = (current_page - 1) * per_page
    start_index = start_offset + 1 if total_count else 0
    end_index = start_offset + len(items)
    return {
        "items": items,
        "page": current_page,
        "page_count": len(items),
        "per_page": per_page,
        "per_page_value": pagination_state["per_page_value"],
        "total_items": total_count,
        "total_pages": total_pages,
        "start_index": start_index,
        "end_index": end_index,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
        "previous_page": current_page - 1 if current_page > 1 else None,
        "next_page": current_page + 1 if current_page < total_pages else None,
        "page_numbers": _build_page_display(total_pages, current_page),
        "is_all": False,
    }


def compute_explorer_scope_counts(collection_name, username, db):
    """Count documents in each assignment scope (maintain/assigned_out/incoming)
    for the explorer's scope tabs.

    A never-assigned document has no ``AssignedTo`` field at all (it's only
    ever added via ``$set`` by the assignment-update helpers) - a missing
    field satisfies Mongo's ``{"$nin": [...]}`` (there's nothing to exclude
    on), so the assigned_out clause must explicitly require the field to
    exist before comparing its value, or every never-assigned document gets
    miscounted as assigned out.
    """
    owner_scope = {"$or": [{"User": username}, {"AssignedTo": username}]}
    assigned_out_filter = {
        "$and": [
            owner_scope,
            {"User": username},
            {"$and": [
                {"AssignedTo": {"$exists": True}},
                {"AssignedTo": {"$nin": ["", username]}},
            ]},
        ]
    }
    incoming_filter = {
        "$and": [owner_scope, {"AssignedTo": username}, {"User": {"$ne": username}}]
    }
    collection = db[collection_name]
    total = collection.count_documents(owner_scope)
    assigned_out = collection.count_documents(assigned_out_filter)
    incoming = collection.count_documents(incoming_filter)
    return {
        "maintain": total - assigned_out,
        "assigned_out": assigned_out,
        "incoming": incoming,
    }


def set_flip_display_fields(record, *, raw_value, display_field):
    day_value = parse_flip_day(raw_value)

    if day_value == -999:
        display_value = "No Flip"
        color_value = "#ffcce0"
        class_value = "flip-tone-none"
    elif day_value < 0:
        display_value = "Overdue"
        color_value = "#f25567"
        class_value = "flip-tone-overdue"
    elif day_value < 1:
        display_value = "Flip today"
        color_value = "#fca15b"
        class_value = "flip-tone-today"
    else:
        display_value = f'Flip in {day_value} day{"s" if day_value > 1 else ""}'
        color_value = "#66fa78"
        class_value = "flip-tone-upcoming"

    record[display_field] = display_value
    record["FlipInColor"] = color_value
    record["FlipInClass"] = class_value