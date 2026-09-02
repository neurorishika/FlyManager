# flymanager/app/routes/main.py
import os
from collections import defaultdict
from datetime import datetime, timedelta

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)

from flymanager.app import db
from flymanager.app.routes.auth import admin_required, login_required
from flymanager.app.routes.explorer_utils import (
    get_explorer_pagination_state, paginate_explorer_records)
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_identifier_list, parse_int_value)
from flymanager.app.services import flybase as flybase_service
from flymanager.app.services.scheduler import schedule_daily_flip_reminders
from flymanager.app.services.stock_standardization import (
    get_cached_cross_standardization, get_cached_stock_standardization,
    summarize_genotype_standardization)
from flymanager.utils.labels import generate_label_pdf
from flymanager.utils.mongo import (OperationLockConflict,
                                    get_accessible_crosses,
                                    get_accessible_stocks, get_flip_in,
                                    get_flip_schedule, get_settings,
                                    get_tray_occupancies_bulk,
                                    get_user_activities, get_user_initials, get_user_trays,
                                    hold_operation_lock, write_activity)
from flymanager.utils.mongo_records import delete_owned_documents_if_status
from flymanager.utils.utils import get_datetime_from_str

bp = Blueprint('main', __name__)  # Remove url_prefix to handle root URL

ATTENTION_STATUSES = {'Showing Issues', 'Needs refresh'}
DASHBOARD_PANEL_PAGE_SIZE = 6
ATTENTION_BOARD_PAGE_SIZE = DASHBOARD_PANEL_PAGE_SIZE
TODAYS_SCHEDULE_PAGE_SIZE = DASHBOARD_PANEL_PAGE_SIZE
UPCOMING_SCHEDULE_PAGE_SIZE = DASHBOARD_PANEL_PAGE_SIZE
RECENT_ACTIVITY_PAGE_SIZE = DASHBOARD_PANEL_PAGE_SIZE
ATTENTION_FILTER_OPTIONS = (
    {'value': 'all', 'label': 'All'},
    {'value': 'critical', 'label': 'Overdue'},
    {'value': 'today', 'label': 'Due Today'},
    {'value': 'watch', 'label': 'Review'},
)

# The dashboard needs vial timing and tray-display fields, but none of the
# large phenotype/standardization caches or modification history embedded in
# stock and cross documents.
_DASHBOARD_RECORD_PROJECTION = {
    '_id': 0,
    'UniqueID': 1,
    'User': 1,
    'AssignedTo': 1,
    'Name': 1,
    'Status': 1,
    'TrayID': 1,
    'TrayPosition': 1,
    'CurrentlyAliveVials': 1,
    'NextFlipDates': 1,
}


def _count_alive_vials(item):
    alive_vials = item.get('CurrentlyAliveVials', '')
    if not alive_vials:
        return 0
    return len([value for value in alive_vials.split(',') if value.strip()])


def _parse_flip_day_value(item):
    try:
        return int(get_flip_in(item).split(' ')[0].split(',')[0].strip())
    except (ValueError, AttributeError, IndexError):
        return None


def _format_tray_location(item):
    tray_id = item.get('TrayID') or 'Unassigned'
    tray_position = item.get('TrayPosition')
    if tray_position in (None, ''):
        return tray_id
    return f'{tray_id}-{tray_position}'


def _merge_responsibility_detail(base_detail, item):
    responsibility_detail = item.get('AssignmentScopeDetail')
    if not responsibility_detail:
        return base_detail
    return f'{base_detail} · {responsibility_detail}' if base_detail else responsibility_detail


def _safe_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _format_day_phrase(day_value):
    if day_value == 0:
        return 'Due today'
    if day_value is None:
        return 'No scheduled flip window'
    if day_value < 0:
        overdue_days = abs(day_value)
        return f"{overdue_days} day{'s' if overdue_days != 1 else ''} overdue"
    return f"Due in {day_value} day{'s' if day_value != 1 else ''}"


def _activity_matches(activity_text, *needles):
    lower_text = activity_text.lower()
    return any(needle in lower_text for needle in needles)


def _build_tray_heatmap(tray, occupancy):
    rows = _safe_int(tray.get('Rows', 0))
    columns = _safe_int(tray.get('Columns', 0))
    total_cells = rows * columns
    occupied_cells = 0
    blocked_cells = 0
    alert_cells = 0
    grid_rows = []

    for row_index in range(1, rows + 1):
        row_cells = []
        for column_index in range(1, columns + 1):
            position = str(((column_index - 1) * 10) + row_index)
            item = occupancy.get(position)
            state = 'empty'
            label = ''
            detail = 'Empty position'

            if item:
                occupied_cells += 1
                if item.get('type') == 'blocked':
                    state = 'blocked'
                    blocked_cells += 1
                    label = 'B'
                    detail = (
                        f"Blocked by {item.get('blocked_by_type', 'item')} "
                        f"at {item.get('blocked_by', '?')}"
                    )
                else:
                    item_type = item.get('type', 'item')
                    if item.get('status') in ATTENTION_STATUSES:
                        state = 'alert'
                        alert_cells += 1
                    else:
                        state = item_type
                    label = 'C' if item_type == 'cross' else 'S'
                    detail = item.get('display_name') or item.get('name') or 'Occupied'

                if item.get('status') in ATTENTION_STATUSES and item.get('type') != 'blocked':
                    detail = f"{detail} · {item.get('status')}"

            row_cells.append({
                'position': position,
                'state': state,
                'label': label,
                'detail': detail,
            })

        grid_rows.append({'cells': row_cells})

    usage = round((occupied_cells / total_cells) * 100) if total_cells else 0
    return {
        'tray_id': tray.get('TrayID', ''),
        'name': tray.get('Name', ''),
        'rows': rows,
        'columns': columns,
        'usage': usage,
        'occupied_cells': occupied_cells,
        'blocked_cells': blocked_cells,
        'alert_cells': alert_cells,
        'grid_rows': grid_rows,
        'href': url_for('tray.view_tray', tray_id=tray.get('TrayID', '')),
    }


def _parse_dashboard_page_arg(arg_name, default=1):
    try:
        return max(1, int(request.args.get(arg_name, default)))
    except (TypeError, ValueError):
        return default


def _normalize_attention_filter(value):
    normalized_value = str(value or 'all').strip().lower()
    allowed_filters = {option['value'] for option in ATTENTION_FILTER_OPTIONS}
    if normalized_value in allowed_filters:
        return normalized_value
    return 'all'


def _paginate_dashboard_records(records, *, page_arg_name, per_page):
    return paginate_explorer_records(
        records,
        page=_parse_dashboard_page_arg(page_arg_name),
        per_page=per_page,
        per_page_value=str(per_page),
    )


def _reviewer_position_value(value):
    try:
        return int(float(str(value or '0').strip()))
    except (TypeError, ValueError):
        return 0


def _reviewer_record_sort_key(row):
    return (
        str(row.get('trayID', '')),
        _reviewer_position_value(row.get('trayPosition')),
        str(row.get('name', '')),
        str(row.get('uniqueID', '')),
        str(row.get('subjectLabel', '')),
    )


# Only the fields the reviewer list renders (plus access/dedupe fields) are
# loaded — notably NOT the large PhenotypeCache — so the page never pulls whole
# documents into memory.
_STOCK_REVIEWER_PROJECTION = {
    '_id': 0,
    'UniqueID': 1,
    'User': 1,
    'AssignedTo': 1,
    'Name': 1,
    'Genotype': 1,
    'TrayID': 1,
    'TrayPosition': 1,
    'StandardizationCache': 1,
}
_CROSS_REVIEWER_PROJECTION = {
    '_id': 0,
    'UniqueID': 1,
    'User': 1,
    'AssignedTo': 1,
    'Name': 1,
    'MaleGenotype': 1,
    'FemaleGenotype': 1,
    'TrayID': 1,
    'TrayPosition': 1,
    'StandardizationCache': 1,
}


def _build_standardization_reviewer_row(*, summary, record_type, subject_label,
                                        unique_id, name, genotype, tray_id,
                                        tray_position, assignment_scope_label,
                                        assignment_scope_detail, action_href,
                                        action_label):
    summary = summary or {}
    return {
        'recordType': record_type,
        'recordTypeLabel': 'Stock' if record_type == 'stock' else 'Cross',
        'subjectLabel': subject_label,
        'uniqueID': str(unique_id or ''),
        'name': str(name or ''),
        'genotype': str(genotype or ''),
        'trayID': str(tray_id or ''),
        'trayPosition': str(tray_position or ''),
        'assignmentScopeLabel': str(assignment_scope_label or 'Maintain'),
        'assignmentScopeDetail': str(assignment_scope_detail or 'Owned by you'),
        'issueCount': int(summary.get('issueCount') or 0),
        'unresolvedCount': int(summary.get('unresolvedCount') or 0),
        'unmodeledCount': int(summary.get('unmodeledCount') or 0),
        'topTokens': list(summary.get('topTokens') or []),
        'recommendedReplacements': list(summary.get('recommendedReplacements') or [])[:3],
        'actionHref': action_href,
        'actionLabel': action_label,
    }


def _stock_standardization_summary(stock):
    """Compact standardization summary for a stock, preferring the cache.

    Reads the materialized ``StandardizationCache`` (non-strict, so viewing a
    record never triggers a live recompute even across pipeline drift). Falls
    back to a live compute only when the cache is missing/incompatible — cheap
    now that the bulk path never runs the fuzzy candidate scan.
    """
    cache = get_cached_stock_standardization(stock, strict=False)
    if cache is not None:
        return cache.get('summary')
    return summarize_genotype_standardization(stock.get('Genotype', ''))


def _cross_standardization_summaries(cross):
    """Return (male_summary, female_summary) for a cross, preferring the cache."""
    cache = get_cached_cross_standardization(cross, strict=False)
    if cache is not None:
        return cache.get('male'), cache.get('female')
    return (
        summarize_genotype_standardization(cross.get('MaleGenotype', '')),
        summarize_genotype_standardization(cross.get('FemaleGenotype', '')),
    )


def _build_standardization_reviewer_rows(stocks, crosses):
    rows = []

    for stock in stocks:
        genotype = str(stock.get('Genotype') or '').strip()
        if not genotype:
            continue
        rows.append(
            _build_standardization_reviewer_row(
                summary=_stock_standardization_summary(stock),
                record_type='stock',
                subject_label='Stock genotype',
                unique_id=stock.get('UniqueID'),
                name=stock.get('Name'),
                genotype=genotype,
                tray_id=stock.get('TrayID'),
                tray_position=stock.get('TrayPosition'),
                assignment_scope_label=stock.get('AssignmentScopeLabel'),
                assignment_scope_detail=stock.get('AssignmentScopeDetail'),
                action_href=url_for('stock.view_stock', unique_id=stock.get('UniqueID')),
                action_label='Open Stock',
            )
        )

    for cross in crosses:
        cross_name = str(cross.get('Name') or cross.get('UniqueID') or '')
        male_summary, female_summary = _cross_standardization_summaries(cross)
        for subject_label, genotype_key, parent_summary in (
            ('Male Parent', 'MaleGenotype', male_summary),
            ('Female Parent', 'FemaleGenotype', female_summary),
        ):
            genotype = str(cross.get(genotype_key) or '').strip()
            if not genotype:
                continue
            rows.append(
                _build_standardization_reviewer_row(
                    summary=parent_summary,
                    record_type='cross_parent',
                    subject_label=subject_label,
                    unique_id=cross.get('UniqueID'),
                    name=cross_name,
                    genotype=genotype,
                    tray_id=cross.get('TrayID'),
                    tray_position=cross.get('TrayPosition'),
                    assignment_scope_label=cross.get('AssignmentScopeLabel'),
                    assignment_scope_detail=cross.get('AssignmentScopeDetail'),
                    action_href=url_for('cross.view_cross', unique_id=cross.get('UniqueID')),
                    action_label='Open Cross',
                )
            )

    rows.sort(
        key=lambda row: (
            -int(row.get('issueCount') or 0),
            0 if row.get('recordType') == 'stock' else 1,
            _reviewer_record_sort_key(row),
        )
    )
    return rows

@bp.route('/')
@bp.route('/main')
def index():
    if not session.get("username"):
        return redirect(url_for('auth.login'))
    return redirect(url_for('main.home'))

@bp.route('/home')
@login_required
def home():
    username = session.get("username")
    is_admin = username == 'admin'
    try:
        flybase_reference_status = (
            flybase_service.get_flybase_reference_status(current_app._get_current_object())
            if is_admin
            else None
        )

        today = datetime.now()

        # Load only the fields this page renders. Activity is bounded to the
        # 30-day analysis window instead of replaying the user's full history.
        stocks = get_accessible_stocks(
            username, db, annotate=True, projection=_DASHBOARD_RECORD_PROJECTION
        )
        crosses = get_accessible_crosses(
            username, db, annotate=True, projection=_DASHBOARD_RECORD_PROJECTION
        )
        trays = get_user_trays(username, db)
        activities = get_user_activities(
            username,
            db,
            since=(today - timedelta(days=30)).strftime('%Y-%m-%d %H:%M'),
        )

        # Filter out items that are no longer maintained
        active_stocks = [s for s in stocks if s.get('Status') != 'No longer maintained']
        active_crosses = [c for c in crosses if c.get('Status') != 'No longer maintained']

        # Calculate statistics
        total_stocks = len(active_stocks)
        total_crosses = len(active_crosses)
        total_lines = total_stocks + total_crosses
        
        # Calculate items needing attention
        needs_attention = 0
        status_distribution = defaultdict(int)
        overdue_items = []
        due_today_items = []
        issue_items = []
        attention_items = []
        queue_candidates = []
        due_soon_count = 0
        
        # Calculate vial statistics
        total_stock_vials = 0
        total_cross_vials = 0

        for item_type, items in (('stock', active_stocks), ('cross', active_crosses)):
            for item in items:
                status = item.get('Status', 'Unknown')
                status_distribution[status] += 1
                tray_location = _format_tray_location(item)
                item_name = item.get('Name', '') or item.get('UniqueID', 'Untitled item')
                item_uid = item.get('UniqueID', '')
                type_label = item_type.title()
                view_endpoint = 'stock.view_stock' if item_type == 'stock' else 'cross.view_cross'
                view_href = url_for(view_endpoint, unique_id=item_uid)
                day_value = _parse_flip_day_value(item)
                vial_count = _count_alive_vials(item)
                is_delegated_out = item.get('AssignmentScope') == 'assigned_out'
                viewer_can_maintain = not is_delegated_out

                base_attention_item = {
                    'type': item_type,
                    'type_label': type_label,
                    'id': item_uid,
                    'name': item_name,
                    'tray': tray_location,
                    'view_href': view_href,
                    'responsibility': item.get('AssignmentScopeDetail', ''),
                }

                if day_value == 0:
                    if viewer_can_maintain:
                        due_today_items.append({
                            **base_attention_item,
                            'detail': _merge_responsibility_detail('Due for flip today', item),
                        })
                        attention_items.append({
                            **base_attention_item,
                            'kind': 'today',
                            'priority': 'today',
                            'badge': 'Due today',
                            'detail': _merge_responsibility_detail('Ready for today\'s flip run', item),
                            'action_href': url_for('flip.flip_interface'),
                            'action_label': 'Open flip desk',
                        })
                    else:
                        attention_items.append({
                            **base_attention_item,
                            'kind': 'watch',
                            'priority': 'watch',
                            'badge': 'Assigned out',
                            'detail': _merge_responsibility_detail('Due today', item),
                            'action_href': view_href,
                            'action_label': 'Inspect item',
                        })
                    needs_attention += 1
                elif day_value is not None and day_value < 0:
                    overdue_detail = f"{abs(day_value)} day{'s' if abs(day_value) != 1 else ''} overdue"
                    if viewer_can_maintain:
                        overdue_items.append({
                            **base_attention_item,
                            'days_overdue': abs(day_value),
                            'detail': _merge_responsibility_detail(overdue_detail, item),
                        })
                        attention_items.append({
                            **base_attention_item,
                            'kind': 'critical',
                            'priority': 'critical',
                            'badge': 'Overdue',
                            'days_overdue': abs(day_value),
                            'detail': _merge_responsibility_detail(overdue_detail, item),
                            'action_href': url_for('flip.flip_interface'),
                            'action_label': 'Flip now',
                        })
                    else:
                        attention_items.append({
                            **base_attention_item,
                            'kind': 'critical',
                            'priority': 'critical',
                            'badge': 'Assigned out',
                            'days_overdue': abs(day_value),
                            'detail': _merge_responsibility_detail(overdue_detail, item),
                            'action_href': view_href,
                            'action_label': 'Inspect item',
                        })
                    needs_attention += 1
                elif status in ATTENTION_STATUSES:
                    issue_items.append({
                        **base_attention_item,
                        'status': status,
                        'detail': _merge_responsibility_detail(status, item),
                    })
                    attention_items.append({
                        **base_attention_item,
                        'kind': 'watch',
                        'priority': 'watch',
                        'badge': 'Review' if viewer_can_maintain else 'Assigned out',
                        'detail': _merge_responsibility_detail(status, item),
                        'action_href': view_href,
                        'action_label': 'Inspect item',
                    })
                    needs_attention += 1

                queue_score = 0
                queue_badge = 'Review'
                queue_reason = ''
                queue_action_href = view_href
                queue_action_label = 'Open item'

                if viewer_can_maintain and day_value is not None:
                    if day_value < 0:
                        queue_score = 140 + min(abs(day_value), 14)
                        queue_badge = 'Overdue'
                        queue_reason = _format_day_phrase(day_value)
                        queue_action_href = url_for('flip.flip_interface')
                        queue_action_label = 'Flip now'
                    elif day_value == 0:
                        queue_score = 112
                        queue_badge = 'Today'
                        queue_reason = 'Ready for today\'s flip run'
                        queue_action_href = url_for('flip.flip_interface')
                        queue_action_label = 'Open flip desk'
                    elif day_value <= 3:
                        due_soon_count += 1
                        queue_score = 86 - (day_value * 6)
                        queue_badge = 'Soon'
                        queue_reason = _format_day_phrase(day_value)
                        queue_action_href = url_for('flip.flip_schedule_display')
                        queue_action_label = 'View schedule'

                if status in ATTENTION_STATUSES:
                    queue_score = max(queue_score, 70)
                    queue_badge = queue_badge if queue_badge != 'Review' or queue_reason else 'Review'
                    queue_reason = f"{queue_reason} · {status}" if queue_reason else status
                    if queue_action_href == view_href:
                        queue_action_label = 'Inspect item'

                if queue_score:
                    queue_score += min(vial_count, 4)
                    if item_type == 'cross':
                        queue_score += 2
                    queue_candidates.append({
                        **base_attention_item,
                        'badge': queue_badge,
                        'reason': queue_reason,
                        'score': queue_score,
                        'day_value': 999 if day_value is None else day_value,
                        'vial_count': vial_count,
                        'action_href': queue_action_href,
                        'action_label': queue_action_label,
                    })

                if item_type == 'stock':
                    total_stock_vials += vial_count
                else:
                    total_cross_vials += vial_count

        # Calculate tray usage accounting for blocking
        total_positions = 0
        used_positions = 0
        active_trays = 0
        tray_heatmaps = []
        
        tray_occupancies = get_tray_occupancies_bulk(trays, db)
        for tray in trays:
            tray_id = tray.get('TrayID')
            if tray_id:
                tray_rows = _safe_int(tray.get('Rows', 0))
                tray_columns = _safe_int(tray.get('Columns', 0))
                total_positions += tray_rows * tray_columns
                
                # Get tray occupancy which accounts for blocking
                try:
                    tray_occupancy = tray_occupancies.get(tray.get('UniqueID'), {})
                    tray_usage_count = len(tray_occupancy)
                    used_positions += tray_usage_count
                    if tray_usage_count:
                        active_trays += 1
                    tray_heatmaps.append(_build_tray_heatmap(tray, tray_occupancy))
                except Exception as e:
                    current_app.logger.warning(
                        "Error getting occupancy for tray %s: %s", tray_id, e
                    )

        tray_heatmaps = sorted(
            tray_heatmaps,
            key=lambda tray: (-tray['usage'], -tray['alert_cells'], tray['tray_id']),
        )
        
        tray_usage = round((used_positions / total_positions * 100) if total_positions > 0 else 0)

        # Get schedule for today and upcoming week
        schedule_data = get_flip_schedule(
            username, db, stocks=stocks, crosses=crosses
        )
        
        # Format today's schedule
        today_str = today.strftime('%Y-%m-%d')
        todays_schedule = []
        if today_str in schedule_data:
            # The schedule_data for a date is a list of strings, not a dict
            items = schedule_data[today_str]
            for item_str in items:
                todays_schedule.append({
                    'time': 'Today',
                    'description': item_str
                })

        # Get upcoming schedule (next 7 days)
        upcoming_schedule = {}
        upcoming_schedule_list = []
        for i in range(1, 8):
            future_date = today + timedelta(days=i)
            future_date_str = future_date.strftime('%Y-%m-%d')
            if future_date_str in schedule_data:
                day_name = future_date.strftime('%A')
                items = schedule_data[future_date_str]
                day_payload = {
                    'iso_date': future_date_str,
                    'day_name': day_name,
                    'date': future_date.strftime('%b %d'),
                    'count': len(items),
                    'items': items,
                    'preview': items[:2],
                }
                upcoming_schedule[future_date_str] = day_payload
                upcoming_schedule_list.append(day_payload)

        schedule_today_count = len(todays_schedule)
        upcoming_total = sum(day['count'] for day in upcoming_schedule_list)
        tasks_today_total = max(schedule_today_count, len(due_today_items))

        # Calculate weekly metrics
        one_week_ago = today - timedelta(days=7)
        parsed_activities = []
        for activity in activities:
            try:
                activity_time = get_datetime_from_str(activity['timestamp'])
            except (ValueError, KeyError, TypeError):
                continue
            parsed_activities.append({
                'datetime': activity_time,
                'activity': activity.get('activity', ''),
            })

        parsed_activities.sort(key=lambda item: item['datetime'], reverse=True)

        # Count flips in the last week, plus a per-date series over the last 30
        # days for the monthly activity chart (a 7-day view is too sparse to
        # show batching rhythm).
        one_month_ago = today - timedelta(days=30)
        weekly_flips = 0
        flips_by_day = defaultdict(int)
        flips_by_date = defaultdict(int)

        for activity in parsed_activities:
            activity_time = activity['datetime']
            activity_text = activity['activity']
            if activity_time >= one_month_ago and 'flipped' in activity_text.lower():
                flips_by_date[activity_time.date()] += 1
                if activity_time >= one_week_ago:
                    weekly_flips += 1
                    day_name = activity_time.strftime('%A')
                    flips_by_day[day_name] += 1

        # Process activities by date
        activity_groups = []
        activity_group_map = {}
        for activity in parsed_activities[:12]:
            date_key = activity['datetime'].strftime('%Y-%m-%d')
            if date_key not in activity_group_map:
                group = {'date': date_key, 'entries': []}
                activity_group_map[date_key] = group
                activity_groups.append(group)
            activity_group_map[date_key]['entries'].append({
                'timestamp': activity['datetime'].strftime('%H:%M'),
                'activity': activity['activity'],
            })

        my_queue = sorted(
            queue_candidates,
            key=lambda item: (-item['score'], item['day_value'], item['name'].lower()),
        )

        thirty_days_ago = today - timedelta(days=30)
        previous_week_start = today - timedelta(days=14)
        current_week_crosses = 0
        previous_week_crosses = 0
        export_count_30 = 0
        import_count_30 = 0
        latest_workbook_activity = None

        for activity in parsed_activities:
            activity_time = activity['datetime']
            activity_text = activity['activity']

            if activity_time >= one_week_ago and _activity_matches(activity_text, 'added cross'):
                current_week_crosses += 1
            elif previous_week_start <= activity_time < one_week_ago and _activity_matches(activity_text, 'added cross'):
                previous_week_crosses += 1

            if activity_time >= thirty_days_ago and _activity_matches(activity_text, 'downloaded data to excel'):
                export_count_30 += 1
                if latest_workbook_activity is None:
                    latest_workbook_activity = {
                        'label': 'Last export',
                        'time': activity_time.strftime('%b %d'),
                    }
            elif activity_time >= thirty_days_ago and _activity_matches(activity_text, 'uploaded data from file'):
                import_count_30 += 1
                if latest_workbook_activity is None:
                    latest_workbook_activity = {
                        'label': 'Last import',
                        'time': activity_time.strftime('%b %d'),
                    }

        attention_priority = {'critical': 0, 'today': 1, 'watch': 2}
        attention_items = sorted(
            attention_items,
            key=lambda item: (
                attention_priority.get(item['priority'], 9),
                -item.get('days_overdue', 0),
                item['name'].lower(),
            ),
        )
        attention_filter = _normalize_attention_filter(request.args.get('attention_filter'))
        filtered_attention_items = (
            attention_items
            if attention_filter == 'all'
            else [item for item in attention_items if item['kind'] == attention_filter]
        )
        attention_pagination = _paginate_dashboard_records(
            filtered_attention_items,
            page_arg_name='attention_page',
            per_page=ATTENTION_BOARD_PAGE_SIZE,
        )
        schedule_pagination = _paginate_dashboard_records(
            todays_schedule,
            page_arg_name='schedule_page',
            per_page=TODAYS_SCHEDULE_PAGE_SIZE,
        )
        upcoming_schedule_pagination = _paginate_dashboard_records(
            upcoming_schedule_list,
            page_arg_name='upcoming_page',
            per_page=UPCOMING_SCHEDULE_PAGE_SIZE,
        )
        activity_pagination = _paginate_dashboard_records(
            activity_groups,
            page_arg_name='activity_page',
            per_page=RECENT_ACTIVITY_PAGE_SIZE,
        )

        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        max_flips = max(flips_by_day.values()) if flips_by_day else 0
        week_chart = []
        for day in days:
            flips = flips_by_day.get(day, 0)
            height = round((flips / max_flips) * 100) if max_flips else 0
            week_chart.append({
                'label': day,
                'short_label': day[:3],
                'count': flips,
                'height': int(height),
            })

        busiest_day = None
        if max_flips:
            busiest_day = max(week_chart, key=lambda item: item['count'])

        # Monthly chart: one bar per calendar day for the trailing 30 days.
        # Labels are thinned to every 5th day so 30 bars stay readable.
        month_chart = []
        max_daily_flips = max(flips_by_date.values()) if flips_by_date else 0
        for offset in range(29, -1, -1):
            day_date = (today - timedelta(days=offset)).date()
            flips = flips_by_date.get(day_date, 0)
            height = round((flips / max_daily_flips) * 100) if max_daily_flips else 0
            month_chart.append({
                'label': day_date.strftime('%b %d'),
                'short_label': day_date.strftime('%d'),
                'show_label': offset % 5 == 0 or offset == 29,
                'count': flips,
                'height': int(height),
            })

        monthly_flips = sum(flips_by_date.values())
        busiest_date = max(month_chart, key=lambda item: item['count']) if max_daily_flips else None

        healthy_count = status_distribution.get('Healthy', 0)
        healthy_share = round((healthy_count / total_lines) * 100) if total_lines else 0
        status_breakdown = []
        for status, count in sorted(status_distribution.items(), key=lambda item: (-item[1], item[0])):
            share = round((count / total_lines) * 100) if total_lines else 0
            status_breakdown.append({
                'status': status,
                'count': count,
                'share': int(share),
                'slug': status.lower().replace(' ', '-'),
            })

        tray_summary = {
            'usage': tray_usage,
            'used_positions': used_positions,
            'total_positions': total_positions,
            'free_positions': max(total_positions - used_positions, 0),
            'active_trays': active_trays,
            'total_trays': len(trays),
        }

        cross_creation_delta = current_week_crosses - previous_week_crosses
        workbook_touchpoints = import_count_30 + export_count_30
        trend_signals = [
            {
                'title': 'Overdue Drift',
                'value': len(overdue_items),
                'meta': 'lines overdue',
                'detail': f"{due_soon_count} due in the next 3 days",
                'support': (
                    'Recovery needed now.' if overdue_items else
                    'Watch the next 72 hours.' if due_soon_count else
                    'Queue is stable.'
                ),
                'tone': 'critical' if overdue_items else 'watch' if due_soon_count else 'calm',
                'icon': 'fas fa-wave-square',
            },
            {
                'title': 'Cross Creation',
                'value': current_week_crosses,
                'meta': 'crosses this week',
                'detail': f"vs {previous_week_crosses} in the previous 7 days",
                'support': (
                    f"{cross_creation_delta:+d} week-over-week" if cross_creation_delta else
                    'Flat week-over-week'
                ),
                'tone': 'today' if cross_creation_delta > 0 else 'watch' if cross_creation_delta < 0 else 'calm',
                'icon': 'fas fa-dna',
            },
            {
                'title': 'Import / Export',
                'value': workbook_touchpoints,
                'meta': 'workbook touchpoints in 30 days',
                'detail': f"{import_count_30} imports, {export_count_30} exports",
                'support': (
                    f"{latest_workbook_activity['label']} {latest_workbook_activity['time']}" if latest_workbook_activity else
                    'No recent workbook activity'
                ),
                'tone': 'watch' if workbook_touchpoints else 'calm',
                'icon': 'fas fa-file-alt',
            },
        ]

        last_activity = parsed_activities[0] if parsed_activities else None
        snapshot = {
            'active_lines': total_lines,
            'tasks_today': tasks_today_total,
            'attention_count': len(attention_items),
            'upcoming_count': upcoming_total,
            'healthy_share': healthy_share,
            'weekly_average': round(weekly_flips / 7, 1),
            'last_activity_at': last_activity['datetime'].strftime('%b %d, %H:%M') if last_activity else 'No recent activity',
            'last_activity_copy': last_activity['activity'] if last_activity else 'Nothing logged yet.',
        }

        if attention_items:
            hero_copy = (
                f"{len(attention_items)} item{'s' if len(attention_items) != 1 else ''} need attention across "
                f"{total_lines} active line{'s' if total_lines != 1 else ''}. Start with overdue flips, then clear today's queue."
            )
        elif tasks_today_total or upcoming_total:
            hero_copy = (
                f"Today's work is steady: {tasks_today_total} scheduled task{'s' if tasks_today_total != 1 else ''} today and "
                f"{upcoming_total} more over the next week."
            )
        else:
            hero_copy = (
                f"The colony is stable with {total_lines} active line{'s' if total_lines != 1 else ''}. "
                "Use the dashboard to review inventory health, tray headroom, and recent changes."
            )

        suggestions = []
        if overdue_items:
            suggestions.append({
                'title': f"Clear {len(overdue_items)} overdue item{'s' if len(overdue_items) != 1 else ''}",
                'copy': 'Work through the oldest flip delays first so tray timing stays recoverable.',
                'href': url_for('flip.flip_interface'),
                'label': 'Open flip desk',
                'icon': 'fas fa-exclamation-triangle',
                'tone': 'critical',
            })
        if schedule_today_count or due_today_items:
            suggestions.append({
                'title': 'Run today\'s queue',
                'copy': 'Use the schedule view to batch the day and avoid context switching between trays.',
                'href': url_for('flip.flip_schedule_display'),
                'label': 'View schedule',
                'icon': 'fas fa-calendar-day',
                'tone': 'today',
            })
        if issue_items:
            suggestions.append({
                'title': f"Review {len(issue_items)} line{'s' if len(issue_items) != 1 else ''} showing issues",
                'copy': 'Inspect flagged stocks and crosses before they turn into missed flips or stale metadata.',
                'href': url_for('stock.stock_explorer'),
                'label': 'Open explorer',
                'icon': 'fas fa-search',
                'tone': 'watch',
            })
        if tray_usage >= 85:
            suggestions.append({
                'title': 'Tray capacity is getting tight',
                'copy': 'Create space now to avoid blocking new setups and recovery work later this week.',
                'href': url_for('tray.tray_management'),
                'label': 'Review trays',
                'icon': 'fas fa-th-large',
                'tone': 'watch',
            })
        if total_crosses == 0 and total_stocks:
            suggestions.append({
                'title': 'Plan the next cross',
                'copy': 'You have stock inventory ready but no active crosses in play.',
                'href': url_for('cross.add_cross'),
                'label': 'Create cross',
                'icon': 'fas fa-heart',
                'tone': 'calm',
            })
        if is_admin:
            suggestions.append({
                'title': 'Protect the current dataset',
                'copy': 'Export the workbook before large imports or metadata updates.',
                'href': url_for('data.download_data_route'),
                'label': 'Download data',
                'icon': 'fas fa-file-export',
                'tone': 'calm',
            })
        if is_admin and flybase_reference_status and flybase_reference_status.get('status_tone') != 'success':
            suggestions.insert(0, {
                'title': f"FlyBase sync {flybase_reference_status.get('status_label', 'needs review').lower()}",
                'copy': flybase_reference_status.get('status_message', 'Review the FlyBase sync controls and rerun the latest release refresh if needed.'),
                'href': url_for('settings.admin_settings'),
                'label': 'Open admin settings',
                'icon': 'fas fa-database',
                'tone': 'critical' if flybase_reference_status.get('status_tone') == 'danger' else 'watch',
            })
        if not suggestions:
            suggestions.append({
                'title': 'Explore the live inventory',
                'copy': 'Nothing is urgent right now. Use the explorers to audit line health and tray placement.',
                'href': url_for('stock.stock_explorer'),
                'label': 'Open stocks',
                'icon': 'fas fa-vial',
                'tone': 'calm',
            })
        suggestions = suggestions[:4]

        # Prepare stats for template
        stats = {
            'total_stocks': total_stocks,
            'total_crosses': total_crosses,
            'needs_attention': needs_attention,
            'tray_usage': tray_usage,
            'status_distribution': status_distribution,
            'total_stock_vials': total_stock_vials,
            'total_cross_vials': total_cross_vials,
            'total_vials': total_stock_vials + total_cross_vials,
            'weekly_flips': weekly_flips,
            'flips_by_day': dict(flips_by_day)
        }

        return render_template(
            "home.html",
            username=username,
            activities=activity_pagination['items'],
            activity_pagination=activity_pagination,
            schedule=schedule_pagination['items'],
            schedule_pagination=schedule_pagination,
            upcoming_schedule=upcoming_schedule,
            upcoming_schedule_list=upcoming_schedule_pagination['items'],
            upcoming_schedule_pagination=upcoming_schedule_pagination,
            overdue_items=sorted(overdue_items, key=lambda x: x['days_overdue'], reverse=True),
            due_today_items=due_today_items,
            issue_items=issue_items,
            attention_items=attention_pagination['items'],
            attention_pagination=attention_pagination,
            attention_filter=attention_filter,
            attention_filter_options=ATTENTION_FILTER_OPTIONS,
            suggestions=suggestions,
            snapshot=snapshot,
            tray_summary=tray_summary,
            tray_heatmaps=tray_heatmaps,
            my_queue=my_queue,
            trend_signals=trend_signals,
            month_chart=month_chart,
            monthly_flips=monthly_flips,
            busiest_date=busiest_date,
            busiest_day=busiest_day,
            status_breakdown=status_breakdown,
            hero_copy=hero_copy,
            is_admin=is_admin,
            flybase_reference_status=flybase_reference_status,
            stats=stats,
            today_date=today.strftime('%B %d, %Y')
        )

    except Exception as e:
        current_app.logger.exception("Error generating homepage for %s: %s", username, e)
        return render_template(
            "home.html",
            username=username,
            activities=[],
            activity_pagination=_paginate_dashboard_records(
                [],
                page_arg_name='activity_page',
                per_page=RECENT_ACTIVITY_PAGE_SIZE,
            ),
            schedule=[],
            schedule_pagination=_paginate_dashboard_records(
                [],
                page_arg_name='schedule_page',
                per_page=TODAYS_SCHEDULE_PAGE_SIZE,
            ),
            upcoming_schedule={},
            upcoming_schedule_list=[],
            upcoming_schedule_pagination=_paginate_dashboard_records(
                [],
                page_arg_name='upcoming_page',
                per_page=UPCOMING_SCHEDULE_PAGE_SIZE,
            ),
            overdue_items=[],
            due_today_items=[],
            issue_items=[],
            attention_items=[],
            attention_pagination=_paginate_dashboard_records(
                [],
                page_arg_name='attention_page',
                per_page=ATTENTION_BOARD_PAGE_SIZE,
            ),
            attention_filter='all',
            attention_filter_options=ATTENTION_FILTER_OPTIONS,
            suggestions=[],
            snapshot={
                'active_lines': 0,
                'tasks_today': 0,
                'attention_count': 0,
                'upcoming_count': 0,
                'healthy_share': 0,
                'weekly_average': 0,
                'last_activity_at': 'No recent activity',
                'last_activity_copy': 'Nothing logged yet.',
            },
            tray_summary={
                'usage': 0,
                'used_positions': 0,
                'total_positions': 0,
                'free_positions': 0,
                'active_trays': 0,
                'total_trays': 0,
            },
            tray_heatmaps=[],
            my_queue=[],
            trend_signals=[],
            month_chart=[],
            monthly_flips=0,
            busiest_date=None,
            busiest_day=None,
            status_breakdown=[],
            hero_copy='The dashboard is temporarily unavailable, but core navigation is still accessible.',
            is_admin=is_admin,
            flybase_reference_status=None,
            stats={
                'total_stocks': 0, 
                'total_crosses': 0, 
                'needs_attention': 0, 
                'tray_usage': 0, 
                'status_distribution': {},
                'total_stock_vials': 0,
                'total_cross_vials': 0,
                'total_vials': 0,
                'weekly_flips': 0,
                'flips_by_day': {}
            },
            today_date=datetime.now().strftime('%B %d, %Y')
        )


@bp.route('/reviewer')
@login_required
def standardization_reviewer():
    username = session.get('username')
    pagination_state = get_explorer_pagination_state(
        session_key='standardization_reviewer_pagination',
    )

    try:
        stocks = get_accessible_stocks(
            username, db, annotate=True, projection=_STOCK_REVIEWER_PROJECTION
        )
        crosses = get_accessible_crosses(
            username, db, annotate=True, projection=_CROSS_REVIEWER_PROJECTION
        )
        reviewer_rows = _build_standardization_reviewer_rows(stocks, crosses)
        pagination = paginate_explorer_records(
            reviewer_rows,
            page=pagination_state['page'],
            per_page=pagination_state['per_page'],
            per_page_value=pagination_state['per_page_value'],
        )
        pagination['per_page_options'] = pagination_state['per_page_options']
        summary = {
            'totalTargets': len(reviewer_rows),
            'stockTargets': sum(1 for row in reviewer_rows if row['recordType'] == 'stock'),
            'crossParentTargets': sum(1 for row in reviewer_rows if row['recordType'] == 'cross_parent'),
            'flaggedTargets': sum(1 for row in reviewer_rows if row['issueCount']),
            'cleanTargets': sum(1 for row in reviewer_rows if not row['issueCount']),
            'totalIssues': sum(int(row['issueCount'] or 0) for row in reviewer_rows),
            'totalUnresolved': sum(int(row['unresolvedCount'] or 0) for row in reviewer_rows),
            'totalUnmodeled': sum(int(row['unmodeledCount'] or 0) for row in reviewer_rows),
            'targetsWithRecommendations': sum(1 for row in reviewer_rows if row['recommendedReplacements']),
        }
    except Exception as exc:
        current_app.logger.exception('Error loading reviewer data for %s: %s', username, exc)
        pagination = paginate_explorer_records(
            [],
            page=pagination_state['page'],
            per_page=pagination_state['per_page'],
            per_page_value=pagination_state['per_page_value'],
        )
        pagination['per_page_options'] = pagination_state['per_page_options']
        reviewer_rows = []
        summary = {
            'totalTargets': 0,
            'stockTargets': 0,
            'crossParentTargets': 0,
            'flaggedTargets': 0,
            'cleanTargets': 0,
            'totalIssues': 0,
            'totalUnresolved': 0,
            'totalUnmodeled': 0,
            'targetsWithRecommendations': 0,
        }

    return render_template(
        'stock/standardization_overview.html',
        username=username,
        review_rows=pagination['items'],
        pagination=pagination,
        summary=summary,
    )

# Route to manually trigger the reminder task for testing
@bp.route('/test_send_reminder', methods=['POST'])
@login_required
@admin_required
@limiter.limit("2 per hour")
def test_send_reminder_route():
    app = current_app._get_current_object()
    try:
        with hold_operation_lock(
            db,
            key="maintenance:daily-flip-reminder",
            actor=session.get("username"),
            label="Daily flip reminder",
            ttl_seconds=1800,
            conflict_message="The daily flip reminder is already running (scheduled or manual). Please wait for it to finish before retrying.",
        ):
            schedule_daily_flip_reminders(app)
        return jsonify({"status": "success"})
    except OperationLockConflict as exc:
        return jsonify({"status": "error", "message": str(exc)}), 409
    except Exception as e:
        current_app.logger.exception("Error triggering test reminder: %s", e)
        return jsonify({"status": "error"}), 500

@bp.route('/update_theme', methods=['POST'])
@bp.route('/main/update_theme', methods=['POST'])
@login_required
@limiter.limit("30 per minute")
def update_theme():
    if request.is_json:
        data = get_json_payload()
        theme = data.get('theme')
        if theme in ['light', 'dark']:
            session['theme'] = theme
            return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 400

@bp.route('/user_guide')
def user_guide():
    return render_template('utilities/user_guide.html')


@bp.route("/generate_labels", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def generate_labels():
    """Generate a single label PDF for a mixed cart of stocks and crosses.

    The shared explorer cart can hold both item types at once, so this
    route (unlike the old per-type stock/cross label routes it replaces)
    merges accessible stocks and crosses by (uid, type) pairs and hands
    generate_label_pdf() one combined, tray-ordered list.
    """
    username = session.get("username")
    fallback_redirect = request.referrer or url_for("main.home")
    try:
        selected_uids_str = request.form.get("selected_uids")
        item_types_str = request.form.get("item_types")
        quantities_str = request.form.get("quantities")
        blank_spaces = parse_int_value(
            request.form.get("blank_spaces", 0),
            field_name="Blank spaces",
            minimum=0,
            maximum=200,
        )

        if not selected_uids_str or not item_types_str or not quantities_str:
            flash("Missing selected items, types, or quantities.", "error")
            return redirect(fallback_redirect)

        selected_uids = selected_uids_str.split(",")
        item_types = item_types_str.split(",")
        quantities = [int(q) for q in quantities_str.split(",")]

        if not (len(selected_uids) == len(item_types) == len(quantities)):
            flash("Mismatch between selected items, types, and quantities.", "error")
            return redirect(fallback_redirect)

        if not set(item_types) <= {"stock", "cross"}:
            flash("Invalid item type in label selection.", "error")
            return redirect(fallback_redirect)

        user_initials = get_user_initials(username, db)

        record_maps = {
            "stock": {str(s["UniqueID"]): s for s in get_accessible_stocks(username, db)},
            "cross": {str(c["UniqueID"]): c for c in get_accessible_crosses(username, db)},
        }

        selected_items = []
        selected_item_types = []
        for uid, item_type, quantity in zip(selected_uids, item_types, quantities):
            record = record_maps[item_type].get(uid)
            if record and quantity > 0:
                selected_items.extend([record] * quantity)
                selected_item_types.extend([item_type] * quantity)

        if not selected_items:
            flash("No valid items selected for label generation.", "warning")
            return redirect(fallback_redirect)

        # Sort the combined stock+cross selection by TrayID/TrayPosition so
        # labels print in physical tray order regardless of item type.
        order = sorted(
            range(len(selected_items)),
            key=lambda i: (
                str(selected_items[i].get("TrayID", "")),
                int(float(selected_items[i].get("TrayPosition") or 0)),
            ),
        )
        selected_items = [selected_items[i] for i in order]
        selected_item_types = [selected_item_types[i] for i in order]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"{username}_labels_{timestamp}.pdf"
        labels_dir = os.path.join(current_app.static_folder, "generated_labels")
        os.makedirs(labels_dir, exist_ok=True)
        pdf_full_path = os.path.join(labels_dir, pdf_filename)

        generate_label_pdf(
            pdf_full_path,
            user_initials,
            selected_items,
            selected_item_types,
            blank_spaces,
            len(selected_items),
        )

        pdf_url = url_for("static", filename=f"generated_labels/{pdf_filename}")

        write_activity(
            username, f"Generated labels for {len(selected_items)} items", db
        )

        return redirect(pdf_url)
    except ValueError as ve:
        flash(f"Invalid input: {ve}", "error")
        return redirect(fallback_redirect)
    except Exception as e:
        current_app.logger.exception("Error generating labels for %s: %s", username, e)
        flash(f"Error generating labels: {e}", "error")
        return redirect(fallback_redirect)


@bp.route("/delete_items_permanently", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def delete_items_permanently():
    """Permanently delete a mixed cart of stocks/crosses.

    Only items with Status "No longer maintained" are deleted; everything
    else is reported as skipped. ``itemTypes`` is a list parallel to
    ``uniqueIDs`` since the underlying delete only touches one collection
    (stocks or crosses) at a time.

    Expected JSON payload:
    {
        "uniqueIDs": ["uid1", "uid2", ...],
        "itemTypes": ["stock", "cross", ...]
    }
    """
    username = session.get("username")
    try:
        data = get_json_payload()
        unique_ids = normalize_identifier_list(data.get("uniqueIDs", []), field_name="uniqueIDs")
        item_types = data.get("itemTypes", [])
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if len(item_types) != len(unique_ids):
        return jsonify({"success": False, "message": "Mismatch between items and item types"}), 400
    if not set(item_types) <= {"stock", "cross"}:
        return jsonify({"success": False, "message": "Invalid item type"}), 400

    uids_by_type = {"stock": [], "cross": []}
    for uid, item_type in zip(unique_ids, item_types):
        uids_by_type[item_type].append(uid)

    deleted_uids = []
    skipped_uids = []
    for item_type, collection_name in (("stock", "stocks"), ("cross", "crosses")):
        type_uids = uids_by_type[item_type]
        if not type_uids:
            continue
        type_deleted, type_skipped = delete_owned_documents_if_status(
            collection_name, username, type_uids, db, required_status="No longer maintained",
        )
        deleted_uids.extend(type_deleted)
        skipped_uids.extend(type_skipped)

    if deleted_uids:
        activity_documents = [
            {
                "user": username,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "activity": f"Permanently deleted item {uid}",
            }
            for uid in deleted_uids
        ]
        db["activity"].insert_many(activity_documents)

    deleted_count = len(deleted_uids)
    skipped_count = len(skipped_uids)

    return jsonify(
        {
            "success": True,
            "deleted": deleted_count,
            "skipped": skipped_count,
            "message": f'Successfully deleted {deleted_count} items with status "No longer maintained". Skipped {skipped_count} items.',
        }
    )
