from django.core.management.base import BaseCommand

from core.order_expiry import expire_pending_orders


class Command(BaseCommand):
    help = "Cancel expired unpaid orders and restore their reserved stock exactly once."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **options):
        limit = max(1, min(options["limit"], 5000))
        count = expire_pending_orders(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} pending order(s)."))
