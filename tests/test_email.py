#!/usr/bin/env python3
"""
Test the beautified email function.
"""

import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flymanager.app import create_app
from flymanager.app.services.email import send_flip_reminder_email


def test_beautified_email():
    """Test the beautified email function."""
    print("Creating Flask app...")
    app = create_app()

    print("Testing beautified email function...")
    with app.app_context():
        try:
            send_flip_reminder_email("admin")  # Test with admin user
            print("✅ Beautified email function executed successfully!")
        except Exception as e:
            print(f"❌ Function failed with error: {e}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    test_beautified_email()
