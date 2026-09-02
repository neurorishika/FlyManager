"""Operational introspection for the admin settings page.

The state backup runs on the app's own APScheduler rather than a DSM Task
Scheduler entry, which keeps the schedule inside the stack -- but also means
there is no external UI showing whether it is scheduled or when it last ran.
Nothing outside the process can answer that: APScheduler's job store is in
memory, its own logging sits below Flask's default WARNING threshold, and a
separate interpreter only ever sees an unstarted scheduler singleton. These
helpers run inside the worker, where the answer actually exists.

"Scheduled" and "ran" are deliberately separate. A job that is registered but
failing every night looks identical to a healthy one unless something measures
the age of what it produced.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from flymanager.app.services.state_backup import DEFAULT_DESTINATION

# A daily job that has produced nothing for two days has missed at least one
# run; the slack absorbs a late run or a clock skew without crying wolf.
STATE_BACKUP_STALE_AFTER_HOURS = 48


def describe_scheduled_jobs(scheduler=None):
    """Every registered job with its next run time.

    Reflects the worker that served THIS request. Each gunicorn worker runs
    its own scheduler, which is why the jobs hold a distributed lock rather
    than trusting that only one exists.
    """
    if scheduler is None:
        from flymanager.app import scheduler as app_scheduler
        scheduler = app_scheduler

    running = bool(getattr(scheduler, "running", False))
    jobs = []
    if running:
        for job in scheduler.get_jobs():
            next_run = getattr(job, "next_run_time", None)
            jobs.append({
                "id": job.id,
                "trigger": str(getattr(job, "trigger", "")),
                # None means registered but not scheduled to run -- paused, or
                # a trigger with no future fire time. Shown as such rather
                # than blank, which reads as "fine".
                "next_run_time": next_run.isoformat() if next_run else None,
            })
        jobs.sort(key=lambda entry: (entry["next_run_time"] or "9999", entry["id"]))
    return {"running": running, "jobs": jobs}


def describe_last_state_backup(directory=None, now=None):
    """What the state backup most recently produced, and how long ago."""
    directory = Path(directory or DEFAULT_DESTINATION)
    now = now or datetime.now(timezone.utc)

    archives = sorted(directory.glob("flymanager_state_*.tar.gz")) if directory.is_dir() else []
    if not archives:
        return {"present": False, "archive": None, "bytes": None,
                "age_hours": None, "stale": True, "directory": str(directory)}

    newest = archives[-1]
    stat = newest.stat()
    # Age comes from the timestamp IN THE NAME, falling back to mtime. The
    # name records when the backup was taken; mtime records when this copy of
    # the file was written, which a restore, an rsync or a Drive round-trip
    # will happily reset to now -- making a stale backup look fresh.
    taken = None
    match = re.search(r"(\d{8}T\d{6})Z", newest.name)
    if match:
        taken = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc)
    if taken is None:
        taken = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    age_hours = (now - taken).total_seconds() / 3600.0
    return {
        "present": True,
        "archive": newest.name,
        "bytes": stat.st_size,
        "age_hours": round(age_hours, 1),
        "stale": age_hours > STATE_BACKUP_STALE_AFTER_HOURS,
        "directory": str(directory),
    }
