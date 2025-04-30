# flymanager/app/routes/main.py
from flask import Blueprint, render_template, session, redirect, url_for
from flymanager.app import db
from flymanager.utils.mongo import get_user_activities
from flymanager.app.services.scheduler import schedule_daily_flip_reminders # For testing
from flymanager.app.routes.auth import login_required

bp = Blueprint('main', __name__, url_prefix='/main')

@bp.route('/home')
@login_required
def home():
    username = session.get("username")
    try:
        activities = get_user_activities(username, db)
        activities = activities[-5:][::-1] # Get last 5, reversed (newest first)
    except Exception as e:
        print(f"Error fetching activities for {username}: {e}")
        activities = []
    return render_template("home.html", username=username, activities=activities)

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
