from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Notification, Order, OrderStatusHistory


class Command(BaseCommand):
    help = "Cancel paid orders whose seller acceptance deadline has expired."
    def handle(self, *args, **options):
        count = 0
        for order in Order.objects.filter(status="paid", acceptance_deadline__lt=timezone.now()).prefetch_related("items__listing"):
            previous = order.status
            order.status, order.cancellation_reason = "cancelled", "Seller acceptance deadline expired."
            order.save(update_fields=["status", "cancellation_reason"])
            for item in order.items.all():
                item.listing.quantity += item.quantity; item.listing.is_active = True; item.listing.save(update_fields=["quantity", "is_active"])
            OrderStatusHistory.objects.create(order=order, from_status=previous, to_status="cancelled", reason=order.cancellation_reason, metadata={"automatic": True})
            Notification.objects.create(user=order.buyer, type="order", title=f"Order #{order.id} cancelled", message=order.cancellation_reason, action_url=f"/app/orders/{order.id}")
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Expired {count} order(s)."))
