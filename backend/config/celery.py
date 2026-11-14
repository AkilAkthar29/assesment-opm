import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("product_ops")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.beat_schedule = {
    "drain-catalog": {"task": "catalog.drain", "schedule": 2, "args": ["catalog"], "options": {"queue": "catalog", "expires": 10}},
    "drain-webhooks": {"task": "catalog.drain", "schedule": 2, "args": ["webhooks"], "options": {"queue": "webhooks", "expires": 10}},
}


@app.task(name="catalog.drain")
def drain(queue):
    if queue not in ('catalog', 'webhooks'):
        raise ValueError('Unknown queue')
    from catalog.worker import claim, process
    kinds = ("delivery", "test") if queue == "webhooks" else ("import", "clear")
    from catalog.db import connect

    with connect() as lock:
        lock_id = 782420 if queue == "catalog" else 782421
        if not lock.execute("SELECT pg_try_advisory_lock(?) AS acquired", (lock_id,)).fetchone()["acquired"]:
            return
        try:
            lock.execute(
                "UPDATE jobs SET state='queued', stage='Queued' WHERE state='running' AND kind IN (?, ?)",
                kinds,
            )
            # A session lock survives this commit. Recovery becomes visible before
            # claim(), without releasing the queue lock during parsing or writing.
            lock.raw.commit()
            for _ in range(20):
                job = claim(kinds)
                if not job:
                    break
                process(job)
        finally:
            lock.execute('SELECT pg_advisory_unlock(?)', (lock_id,))

