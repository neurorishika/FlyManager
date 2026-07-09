import time

from flymanager.app import db, get_all_users
from flymanager.app.services.email import send_flip_reminder_email


def schedule_daily_flip_reminders(app):
    """
    Scheduled task function to send flip reminders to all users.
    Needs to run within an app context. Concurrency safety is handled by
    the distributed lock in flymanager.app.run_locked_scheduled_job, which
    wraps every call site of this function.
    """
    with app.app_context():
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Running daily flip reminder task...")

        try:
            all_users_dict = get_all_users(db)
            if not all_users_dict:
                print(f"[{timestamp}] No users found in the database.")
                return

            all_usernames = list(all_users_dict.keys())
            print(f"[{timestamp}] Found users: {all_usernames}")

            for username in all_usernames:
                print(f"[{timestamp}] Sending reminder for user: {username}")
                send_flip_reminder_email(username)
            print(f"[{timestamp}] Finished sending daily flip reminders.")

        except Exception as e:
            print(f"[{timestamp}] Error during scheduled flip reminder task: {e}")


# The job addition and starting is handled in app/__init__.py
