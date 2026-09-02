"""Admin-visible answer to "is the backup actually scheduled, and did it run?"

The state backup moved off DSM Task Scheduler onto the app's own APScheduler,
which removed an out-of-stack dependency but also removed DSM's email-on-
failure. From outside the process there is no way to see whether a cron job
registered: APScheduler's job store is in memory, its own logging sits below
Flask's default WARNING threshold, and a fresh `python -c` sees an unstarted
scheduler singleton. This is the introspection that closes that gap.

"Scheduled" and "ran" are different questions, and a backup that is scheduled
but silently failing is the dangerous case, so both are reported.
"""
import datetime
from unittest.mock import patch

import pytest

from flymanager.app.services.diagnostics import (describe_last_state_backup,
                                                 describe_scheduled_jobs)


class _Job:
    def __init__(self, id, next_run_time, trigger="cron[hour='2', minute='15']"):
        self.id = id
        self.next_run_time = next_run_time
        self.trigger = trigger

    def __str__(self):
        return f"{self.id} (trigger: {self.trigger})"


class _Scheduler:
    def __init__(self, running=True, jobs=()):
        self.running = running
        self._jobs = list(jobs)

    def get_jobs(self):
        return self._jobs


def test_it_reports_each_job_with_its_next_run():
    when = datetime.datetime(2026, 9, 3, 2, 15, tzinfo=datetime.timezone.utc)
    sched = _Scheduler(jobs=[_Job("daily_state_backup_job", when)])
    result = describe_scheduled_jobs(scheduler=sched)
    assert result["running"] is True
    assert result["jobs"][0]["id"] == "daily_state_backup_job"
    assert result["jobs"][0]["next_run_time"].startswith("2026-09-03T02:15")


def test_a_stopped_scheduler_is_reported_as_such_not_as_an_empty_list():
    """An empty job list and a dead scheduler look identical otherwise, and
    they need very different responses."""
    result = describe_scheduled_jobs(scheduler=_Scheduler(running=False))
    assert result["running"] is False
    assert result["jobs"] == []


def test_a_job_with_no_next_run_is_flagged_rather_than_shown_blank():
    sched = _Scheduler(jobs=[_Job("paused_job", None)])
    assert describe_scheduled_jobs(scheduler=sched)["jobs"][0]["next_run_time"] is None


def test_the_last_state_backup_is_reported_with_its_age(tmp_path):
    archive = tmp_path / "flymanager_state_20260902T183020Z.tar.gz"
    archive.write_bytes(b"x" * 2048)
    (tmp_path / "flymanager_env_20260902T183020Z.env.enc").write_bytes(b"y")
    now = datetime.datetime(2026, 9, 3, 2, 20, tzinfo=datetime.timezone.utc)
    result = describe_last_state_backup(directory=tmp_path, now=now)
    assert result["present"] is True
    assert result["archive"] == archive.name
    assert result["bytes"] == 2048
    assert 7 <= result["age_hours"] <= 8


def test_a_missing_backup_directory_is_reported_not_raised(tmp_path):
    """A fresh deploy has no backups yet; the panel must still render."""
    result = describe_last_state_backup(directory=tmp_path / "nope")
    assert result["present"] is False
    assert result.get("archive") is None


def test_a_stale_backup_is_marked_stale(tmp_path):
    """The whole point: a backup that stopped running looks exactly like one
    that is working, unless something measures its age."""
    (tmp_path / "flymanager_state_20260101T000000Z.tar.gz").write_bytes(b"x")
    now = datetime.datetime(2026, 9, 3, tzinfo=datetime.timezone.utc)
    result = describe_last_state_backup(directory=tmp_path, now=now)
    assert result["stale"] is True


def test_a_recent_backup_is_not_stale(tmp_path):
    (tmp_path / "flymanager_state_20260902T183020Z.tar.gz").write_bytes(b"x")
    now = datetime.datetime(2026, 9, 2, 20, 0, tzinfo=datetime.timezone.utc)
    assert describe_last_state_backup(directory=tmp_path, now=now)["stale"] is False


def _app():
    from flymanager.app import create_app
    with patch("flymanager.app.get_settings", return_value={
            "lab_info": {"lab_name": "L", "admin_name": "A", "admin_email": "a@b.c"},
            "theme": {"accent_color": "#000", "dark_mode": False}}):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _client(app, username):
    client = app.test_client()
    with client.session_transaction() as session:
        session["username"] = username
    return client


def test_a_non_admin_cannot_read_the_diagnostics():
    """Scheduling and backup state are operational detail, not user detail."""
    app = _app()
    response = _client(app, "alice").get("/settings/scheduler")
    assert response.status_code in (302, 403)


def test_an_admin_gets_both_halves_of_the_answer():
    app = _app()
    response = _client(app, "admin").get("/settings/scheduler")
    assert response.status_code == 200
    payload = response.get_json()
    assert "running" in payload["scheduler"] and "jobs" in payload["scheduler"]
    assert "present" in payload["state_backup"] and "stale" in payload["state_backup"]


def test_the_admin_settings_page_renders_the_panel():
    app = _app()
    body = _client(app, "admin").get("/settings").data.decode()
    assert "Scheduled Tasks" in body
    assert "schedulerDiagnostics" in body
