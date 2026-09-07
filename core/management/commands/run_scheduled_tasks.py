from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run idempotent recurring marketplace maintenance tasks."

    def handle(self, *args, **options):
        call_command("expire_pending_orders")
        call_command("expire_order_acceptance")
        call_command("expire_listings")
        call_command("process_outbox", limit=250)
        self.stdout.write(self.style.SUCCESS("Scheduled maintenance completed."))
