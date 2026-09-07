from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Run read-only integrity checks against a restored production-like database."

    def handle(self, *args, **options):
        required = {"django_migrations", "core_user", "core_listing", "core_order", "core_ledgertransaction", "core_outboxmessage"}
        tables = set(connection.introspection.table_names())
        missing = required - tables
        if missing:
            raise CommandError(f"Restore is missing required tables: {sorted(missing)}")
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            names = [f"{migration.app_label}.{migration.name}" for migration, _backwards in pending]
            raise CommandError(f"Restore has pending migrations: {names}")
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM core_user")
            users = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM core_order")
            orders = cursor.fetchone()[0]
        self.stdout.write(self.style.SUCCESS(f"Restore verification passed; users={users}, orders={orders}."))
