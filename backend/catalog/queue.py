"""Drain durable PostgreSQL jobs under a per-queue session lock."""
import logging

logger = logging.getLogger(__name__)

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
                logger.info('Processing job=%s kind=%s queue=%s', job['id'], job['kind'], queue)
                process(job)
                logger.info('Finished job=%s queue=%s', job['id'], queue)
        finally:
            lock.execute('SELECT pg_advisory_unlock(?)', (lock_id,))
