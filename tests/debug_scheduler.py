from flymanager.app import create_app

app = create_app()

with app.app_context():
    from flymanager.app import scheduler

    if scheduler.running:
        jobs = scheduler.get_jobs()
        print(f"Scheduler is running with {len(jobs)} jobs:")
        for job in jobs:
            print(f"  - Job ID: {job.id}")
            print(f"    Function: {job.func}")
            print(f"    Trigger: {job.trigger}")
            print(f"    Next run: {job.next_run_time}")
            print()
    else:
        print("Scheduler is not running")
