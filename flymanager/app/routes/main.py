# flymanager/app/routes/main.py
from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify
from datetime import datetime, timedelta
from collections import defaultdict
from flymanager.app import db
from flymanager.utils.mongo import (
    get_user_activities, get_user_stocks, get_user_crosses, 
    get_user_trays, get_flip_schedule, get_flip_in, get_settings, get_tray_occupancy
)
from flymanager.app.services.scheduler import schedule_daily_flip_reminders
from flymanager.app.routes.auth import login_required
from flymanager.utils.utils import get_datetime_from_str

bp = Blueprint('main', __name__)  # Remove url_prefix to handle root URL

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
    try:
        # Get all user data
        stocks = get_user_stocks(username, db)
        crosses = get_user_crosses(username, db)
        trays = get_user_trays(username, db)
        activities = get_user_activities(username, db)
        settings = get_settings(db)

        # Filter out items that are no longer maintained
        active_stocks = [s for s in stocks if s.get('Status') != 'No longer maintained']
        active_crosses = [c for c in crosses if c.get('Status') != 'No longer maintained']

        # Calculate statistics
        total_stocks = len(active_stocks)
        total_crosses = len(active_crosses)
        
        # Calculate items needing attention
        needs_attention = 0
        status_distribution = defaultdict(int)
        overdue_items = []
        due_today_items = []
        
        # Calculate vial statistics
        total_stock_vials = 0
        total_cross_vials = 0
        
        for stock in active_stocks:
            status = stock.get('Status', 'Unknown')
            status_distribution[status] += 1
            
            flip_in = get_flip_in(stock)
            day_value = -999
            try:
                day_value = int(flip_in.split(' ')[0].split(',')[0].strip())
            except (ValueError, AttributeError):
                pass
                
            if day_value == 0:
                due_today_items.append({
                    'type': 'stock', 
                    'id': stock.get('UniqueID'),
                    'name': stock.get('Name', ''),
                    'tray': f"{stock.get('TrayID', '')}-{stock.get('TrayPosition', '')}"
                })
                needs_attention += 1
            elif day_value < 0 and day_value != -999:
                overdue_items.append({
                    'type': 'stock', 
                    'id': stock.get('UniqueID'),
                    'name': stock.get('Name', ''),
                    'days_overdue': abs(day_value),
                    'tray': f"{stock.get('TrayID', '')}-{stock.get('TrayPosition', '')}"
                })
                needs_attention += 1
            elif status in ['Showing Issues', 'Needs refresh']:
                needs_attention += 1
            
            # Count vials
            alive_vials = stock.get('CurrentlyAliveVials', '')
            if alive_vials:
                total_stock_vials += len(alive_vials.split(', ')) if ',' in alive_vials else 1
        
        for cross in active_crosses:
            status = cross.get('Status', 'Unknown')
            status_distribution[status] += 1
            
            flip_in = get_flip_in(cross)
            day_value = -999
            try:
                day_value = int(flip_in.split(' ')[0].split(',')[0].strip())
            except (ValueError, AttributeError):
                pass
                
            if day_value == 0:
                due_today_items.append({
                    'type': 'cross', 
                    'id': cross.get('UniqueID'),
                    'name': cross.get('Name', ''),
                    'tray': f"{cross.get('TrayID', '')}-{cross.get('TrayPosition', '')}"
                })
                needs_attention += 1
            elif day_value < 0 and day_value != -999:
                overdue_items.append({
                    'type': 'cross', 
                    'id': cross.get('UniqueID'),
                    'name': cross.get('Name', ''),
                    'days_overdue': abs(day_value),
                    'tray': f"{cross.get('TrayID', '')}-{cross.get('TrayPosition', '')}"
                })
                needs_attention += 1
            elif status in ['Showing Issues', 'Needs refresh']:
                needs_attention += 1
            
            # Count vials
            alive_vials = cross.get('CurrentlyAliveVials', '')
            if alive_vials:
                total_cross_vials += len(alive_vials.split(', ')) if ',' in alive_vials else 1

        # Calculate tray usage accounting for blocking
        total_positions = 0
        used_positions = 0
        
        for tray in trays:
            tray_id = tray.get('TrayID')
            if tray_id:
                tray_rows = tray.get('Rows', 0)
                tray_columns = tray.get('Columns', 0)
                total_positions += tray_rows * tray_columns
                
                # Get tray occupancy which accounts for blocking
                try:
                    tray_occupancy = get_tray_occupancy(username, tray_id, db)
                    used_positions += len(tray_occupancy)
                except Exception as e:
                    print(f"Error getting occupancy for tray {tray_id}: {e}")
        
        tray_usage = round((used_positions / total_positions * 100) if total_positions > 0 else 0)

        # Get schedule for today and upcoming week
        schedule_data = get_flip_schedule(username, db)
        today = datetime.now()
        
        # Format today's schedule
        today_str = today.strftime('%Y-%m-%d')
        todays_schedule = []
        if today_str in schedule_data:
            # The schedule_data for a date is a list of strings, not a dict
            items = schedule_data[today_str]
            for item_str in items:
                todays_schedule.append({
                    'time': today.strftime('%H:%M'),
                    'description': item_str
                })

        # Get upcoming schedule (next 7 days)
        upcoming_schedule = {}
        for i in range(1, 8):
            future_date = today + timedelta(days=i)
            future_date_str = future_date.strftime('%Y-%m-%d')
            if future_date_str in schedule_data:
                day_name = future_date.strftime('%A')
                items = schedule_data[future_date_str]
                upcoming_schedule[future_date_str] = {
                    'day_name': day_name,
                    'date': future_date.strftime('%b %d'),
                    'count': len(items),
                    'items': items
                }

        # Calculate weekly metrics
        one_week_ago = today - timedelta(days=7)
        one_week_ago_str = one_week_ago.strftime('%Y-%m-%d')
        
        # Count flips in the last week
        weekly_flips = 0
        flips_by_day = defaultdict(int)
        
        for activity in activities:
            try:
                activity_time = get_datetime_from_str(activity['timestamp'])
                if activity_time >= one_week_ago and 'flipped' in activity['activity'].lower():
                    weekly_flips += 1
                    day_name = activity_time.strftime('%A')
                    flips_by_day[day_name] += 1
            except (ValueError, KeyError):
                pass
        
        # Process activities by date
        processed_activities = {}
        for activity in activities[-10:]:  # Get last 10 activities
            date = get_datetime_from_str(activity['timestamp']).strftime('%Y-%m-%d')
            if date not in processed_activities:
                processed_activities[date] = []
            processed_activities[date].append({
                'timestamp': get_datetime_from_str(activity['timestamp']).strftime('%H:%M'),
                'activity': activity['activity']
            })

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
            activities=processed_activities,
            schedule=todays_schedule,
            upcoming_schedule=upcoming_schedule,
            overdue_items=sorted(overdue_items, key=lambda x: x['days_overdue'], reverse=True),
            due_today_items=due_today_items,
            stats=stats,
            settings=settings,
            today_date=today.strftime('%B %d, %Y')
        )

    except Exception as e:
        print(f"Error generating homepage for {username}: {e}")
        import traceback
        traceback.print_exc()
        return render_template(
            "home.html",
            username=username,
            activities={},
            schedule=[],
            upcoming_schedule={},
            overdue_items=[],
            due_today_items=[],
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
            settings=get_settings(db),
            today_date=datetime.now().strftime('%B %d, %Y')
        )

# Route to manually trigger the reminder task for testing
@bp.route('/test_send_reminder')
@login_required
def test_send_reminder_route():
    try:
        # Run the task directly (requires app context, which we have in a request)
        schedule_daily_flip_reminders()
        return "Test reminder task triggered!"
    except Exception as e:
        print(f"Error triggering test reminder: {e}")
        return f"Error triggering test reminder: {e}", 500

@bp.route('/update_theme', methods=['POST'])
def update_theme():
    if request.is_json:
        data = request.get_json()
        theme = data.get('theme')
        if theme in ['light', 'dark']:
            session['theme'] = theme
            return jsonify({'status': 'success'})
    return jsonify({'status': 'error'}), 400

@bp.route('/user_guide')
def user_guide():
    return render_template('utilities/user_guide.html')
