from django.db import transaction
from django.utils import timezone

from .models import Order
from .order_lifecycle import transition_order


def expire_pending_orders(*, now=None, limit=500):
    """Cancel unpaid reservations whose checkout window elapsed.

    Row locks and stock-restoration timestamps make repeated or concurrent runs
    safe. A payment webhook that wins the lock clears payment_expires_at first.
    """
    now = now or timezone.now()
    order_ids = list(
        Order.objects.filter(status="pending", payment_expires_at__lte=now)
        .order_by("payment_expires_at")
        .values_list("id", flat=True)[:limit]
    )
    expired = 0
    for order_id in order_ids:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(id=order_id)
            if order.status != "pending" or not order.payment_expires_at or order.payment_expires_at > now:
                continue
            transition_order(
                order.id,
                order.buyer,
                "cancelled",
                "Payment window expired before confirmation.",
                {"expired_at": now.isoformat()},
                system=True,
            )
            expired += 1
    return expired
