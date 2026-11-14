"""Redis transports queue drains; PostgreSQL remains the durable job outbox."""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
app = Celery('product_ops')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.conf.beat_schedule = {
    f'drain-{queue}': {
        'task': 'catalog.drain', 'schedule': 2, 'args': [queue],
        'options': {'queue': queue, 'expires': 10},
    }
    for queue in ('catalog', 'webhooks')
}


@app.task(name='catalog.drain')
def drain(queue):
    from catalog.queue import drain as drain_jobs
    return drain_jobs(queue)
