"""
Test route for manually triggering the scheduler job.
Add this to your main routes or create a temporary test route.
"""

from flask import Blueprint, current_app, jsonify
from flymanager.app.services.scheduler import schedule_daily_flip_reminders

# Create a test blueprint
test_bp = Blueprint("test", __name__)


@test_bp.route("/test/scheduler")
def test_scheduler():
    """Manually trigger the scheduled job for testing."""
    try:
        app = current_app._get_current_object()
        schedule_daily_flip_reminders(app)
        return jsonify(
            {"status": "success", "message": "Scheduler job executed successfully"}
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
