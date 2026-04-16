import os
import time

from flymanager.app import db
from flymanager.utils.mongo import add_user, get_all_users


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def wait_for_mongo(timeout_seconds=60, interval_seconds=2):
    """Wait for MongoDB to accept commands before continuing."""
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            db.command("ping")
            print("MongoDB is ready.")
            return True
        except Exception as exc:
            print(f"Waiting for MongoDB: {exc}")
            time.sleep(interval_seconds)

    print("Timed out waiting for MongoDB.")
    return False


def ensure_default_admin():
    """Create an initial operator account when bootstrap env vars are provided."""
    username = os.getenv("FLYMANAGER_ADMIN_USERNAME", "").strip()
    password = os.getenv("FLYMANAGER_ADMIN_PASSWORD", "").strip()
    initials = os.getenv("FLYMANAGER_ADMIN_INITIALS", "").strip()

    if not username:
        return

    if not password or not initials:
        print(
            "Skipping default admin bootstrap because username, password, and initials were not all provided."
        )
        return

    users = get_all_users(db)
    if username in users:
        print(f"Bootstrap user '{username}' already exists.")
        return

    if add_user(username, password, initials, db):
        print(f"Bootstrap user '{username}' created.")
    else:
        print(f"Bootstrap user '{username}' could not be created.")


def main():
    if env_flag("WAIT_FOR_MONGO", True):
        timeout_seconds = int(os.getenv("WAIT_FOR_MONGO_TIMEOUT", "60"))
        interval_seconds = int(os.getenv("WAIT_FOR_MONGO_INTERVAL", "2"))
        if not wait_for_mongo(timeout_seconds=timeout_seconds, interval_seconds=interval_seconds):
            raise SystemExit(1)

    ensure_default_admin()


if __name__ == "__main__":
    main()