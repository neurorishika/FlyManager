from flask import Blueprint, jsonify, render_template, session

from flymanager.app import db
from flymanager.app.routes.auth import login_required
from flymanager.utils.mongo import list_recent_jobs

bp = Blueprint("jobs", __name__, url_prefix="/jobs")


def _serialize_job(job):
    def _iso(value):
        return value.isoformat() if value else None

    return {
        "key": job.get("key"),
        "label": job.get("label"),
        "actor": job.get("actor"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": _iso(job.get("created_at")),
        "started_at": _iso(job.get("started_at")),
        "finished_at": _iso(job.get("finished_at")),
        "metadata": job.get("metadata") or {},
    }


def _jobs_for_viewer(username, *, limit=20):
    actor = None if username == "admin" else username
    return list_recent_jobs(db, actor=actor, limit=limit)


@bp.route("/status.json")
@login_required
def jobs_status():
    """Polling endpoint backing the live job banner.

    Returns the viewer's own jobs (or everyone's, for admin) so the banner
    can show what's running and toast on completion without the frontend
    needing to track individual job keys across page navigations.
    """
    username = session.get("username")
    jobs = [_serialize_job(job) for job in _jobs_for_viewer(username)]
    return jsonify({"jobs": jobs})


@bp.route("")
@login_required
def jobs_history():
    """Background job history page: admins see every job, everyone else sees their own."""
    username = session.get("username")
    jobs = [_serialize_job(job) for job in _jobs_for_viewer(username, limit=100)]
    return render_template(
        "settings/jobs.html",
        jobs=jobs,
        username=username,
        is_admin=(username == "admin"),
    )
