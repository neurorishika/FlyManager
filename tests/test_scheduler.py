#!/usr/bin/env python3
"""
Test script for the scheduled job function.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flymanager.app import create_app
from flymanager.app.services.scheduler import schedule_daily_flip_reminders


def test_scheduler_function():
    """Test the scheduler function directly."""
    print("Creating Flask app...")
    app = create_app()

    print("Testing schedule_daily_flip_reminders function...")
    try:
        schedule_daily_flip_reminders(app)
        print("✅ Function executed successfully!")
    except Exception as e:
        print(f"❌ Function failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_scheduler_function()
