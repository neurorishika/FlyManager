# flymanager/app/services/scheduler.py
from flymanager.app import scheduler, db, get_all_users
from .email import send_flip_reminder_email


def schedule_daily_flip_reminders(app):
    """
    Scheduled task function to send flip reminders to all users.
    Needs to run within an app context.
    """
    with app.app_context():
        print("Running daily flip reminder task...")
        try:
            all_users_dict = get_all_users(db)
            if not all_users_dict:
                print("No users found in the database.")
                return

            all_usernames = list(all_users_dict.keys())
            print(f"Found users: {all_usernames}")

            for username in all_usernames:
                print(f"Sending reminder for user: {username}")
                send_flip_reminder_email(username)
            print("Finished sending daily flip reminders.")

        except Exception as e:
            print(f"Error during scheduled flip reminder task: {e}")


# The job addition and starting is handled in app/__init__.py
