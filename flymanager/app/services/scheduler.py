import time
import os
import fcntl
from flymanager.app import scheduler, db, get_all_users
from flymanager.app.services.email import send_flip_reminder_email


def schedule_daily_flip_reminders(app):
    """
    Scheduled task function to send flip reminders to all users.
    Needs to run within an app context.
    Uses file locking to prevent duplicate execution.
    """
    lock_file_path = "/tmp/flip_reminder_lock"

    try:
        # Create lock file
        lock_file = open(lock_file_path, "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with app.app_context():
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] Running daily flip reminder task (LOCKED)...")

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
            finally:
                # Release lock
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
                os.remove(lock_file_path)

    except IOError:
        # Another instance is already running
        print(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Flip reminder task already running, skipping..."
        )
        return


# The job addition and starting is handled in app/__init__.py
