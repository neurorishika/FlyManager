"""RQ worker entrypoint for background jobs.

Run via: python -m flymanager.app.worker
(or the gunicorn-equivalent for the worker service in compose/Dockerfile)
"""
import os

from rq import Worker

from flymanager.app.jobs import DEFAULT_QUEUE_NAME, get_redis_connection, get_worker_app

# The web process already runs the cron scheduler; the worker process must
# not start a second copy of it.
os.environ.setdefault("ENABLE_SCHEDULER", "0")


def main():
    app = get_worker_app()
    with app.app_context():
        worker = Worker([DEFAULT_QUEUE_NAME], connection=get_redis_connection())
        worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
