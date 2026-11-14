from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from config.env import DATA_DIR
from django.conf import settings
from redis import Redis


class Command(BaseCommand):
    help = 'Check PostgreSQL, migrations, storage and Redis without printing credentials.'

    def handle(self, *args, **options):
        try:
            connection.ensure_connection()
            executor = MigrationExecutor(connection)
            pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if pending:
                raise CommandError('Migrations are pending. Run python manage.py migrate.')
        except CommandError:
            raise
        except Exception as error:
            raise CommandError('PostgreSQL is unavailable. Start the Windows PostgreSQL service and check DATABASE_URL.') from error
        self.stdout.write('PostgreSQL and migrations: OK')
        probe = DATA_DIR / '.storage-probe'
        try:
            probe.write_text('ok', encoding='utf-8')
            probe.unlink()
        except OSError as error:
            raise CommandError('Shared DATA_DIR is not writable.') from error
        self.stdout.write(f'Shared storage: {DATA_DIR}')
        try:
            with Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=3, socket_timeout=3) as broker:
                broker.ping()
        except Exception as error:
            raise CommandError('Redis is unavailable. Start Redis in WSL Ubuntu and check REDIS_URL.') from error
        self.stdout.write('Redis: OK (use celery inspect ping to check workers separately)')
