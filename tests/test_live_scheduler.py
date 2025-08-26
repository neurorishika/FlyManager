#!/usr/bin/env python3
"""
Test the actual Flask-APScheduler integration.
This will run the Flask app briefly and trigger the scheduler.
"""

import sys
import os
import time
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flymanager.app import create_app, scheduler
from flymanager.app.services import scheduler as scheduler_service


def test_live_scheduler():
    """Test the scheduler in a live Flask app context."""
    app = create_app()

    with app.app_context():
        # Remove the existing job if it exists
        try:
            scheduler.remove_job("daily_flip_reminder_job")
            print("Removed existing scheduled job")
        except Exception:
            pass

        # Add a test job that runs every 10 seconds
        print("Adding test job to run every 10 seconds...")
        scheduler.add_job(
            id="test_flip_reminder_job",
            func=scheduler_service.schedule_daily_flip_reminders,
            trigger="interval",
            seconds=10,
            args=[app],
        )

        print("Starting scheduler...")
        if not scheduler.running:
            scheduler.start()

        print("Scheduler is running. Waiting for 30 seconds to see if job executes...")
        print(f"Jobs in scheduler: {[job.id for job in scheduler.get_jobs()]}")

        # Wait and let the scheduler run
        try:
            time.sleep(30)
        except KeyboardInterrupt:
            print("\nStopping scheduler...")
        finally:
            scheduler.shutdown()
            print("Scheduler stopped.")


if __name__ == "__main__":
    test_live_scheduler()
