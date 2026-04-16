from flask import redirect, request, session, url_for

from flymanager.utils.utils import parse_flip_day


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