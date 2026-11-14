"""Check that beat routes both outbox drains through Redis without eager jobs."""
import unittest

from config.celery import app


class CeleryConfigurationTests(unittest.TestCase):
    def test_independent_queues_and_real_broker(self):
        self.assertTrue(app.conf.broker_url.startswith(('redis://', 'rediss://')))
        self.assertFalse(app.conf.task_always_eager)
        schedule = app.conf.beat_schedule
        for queue in ('catalog', 'webhooks'):
            with self.subTest(queue=queue):
                entry = schedule['drain-' + queue]
                self.assertEqual(entry['task'], 'catalog.drain')
                self.assertEqual(entry['args'], [queue])
                self.assertEqual(entry['options']['queue'], queue)
                self.assertEqual(entry['schedule'], 2)
