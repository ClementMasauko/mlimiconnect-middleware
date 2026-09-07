from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .models import AuditLog, Notification, Order, OrderFulfilment, OrderStatusHistory
from .communications import deliver_order_update

SELLER_TRANSITIONS = {"paid": {"accepted", "cancelled"}, "accepted": {"packed", "partially_fulfilled", "cancelled"}, "packed": {"dispatched", "partially_fulfilled"}}
TRANSPORTER_TRANSITIONS = {"dispatched": {"delivered", "failed_delivery"}, "failed_delivery": {"dispatched"}}
BUYER_TRANSITIONS = {"delivered": {"completed", "disputed"}, "partially_fulfilled": {"completed", "disputed"}}
ADMIN_TRANSITIONS = {"pending": {"paid", "cancelled"}, "disputed": {"refunded", "completed"}, "cancelled": {"refunded"}, "failed_delivery": {"refunded", "dispatched"}}


def actor_role(order, actor):
    if actor and (actor.is_staff or actor.user_type == "admin"): return "admin"
    if actor == order.buyer: return "buyer"
    if actor and order.items.filter(listing__seller=actor).exists(): return "seller"
    if actor and hasattr(order, "delivery") and order.delivery.transporter_id == actor.id: return "transporter"
    raise PermissionDenied("You are not permitted to update this order.")


def _restore_items(items, restored_at):
    for item in items.select_related("listing").select_for_update():
        restore_quantity = max(item.quantity - item.fulfilled_quantity, 0)
        if not restore_quantity:
            continue
        item.listing.quantity += restore_quantity
        item.listing.is_active = True
        item.listing.save(update_fields=["quantity", "is_active"])


def restore_fulfilment_stock(fulfilment, restored_at=None):
    if fulfilment.stock_restored_at:
        return False
    restored_at = restored_at or timezone.now()
    _restore_items(fulfilment.items, restored_at)
    fulfilment.stock_restored_at = restored_at
    fulfilment.save(update_fields=["stock_restored_at", "updated_at"])
    return True


def restore_order_stock(order, restored_at=None):
    if order.stock_restored_at:
        return False
    restored_at = restored_at or timezone.now()
    fulfilments = list(order.fulfilments.select_for_update())
    for fulfilment in fulfilments:
        restore_fulfilment_stock(fulfilment, restored_at)
    _restore_items(order.items.filter(fulfilment__isnull=True), restored_at)
    order.stock_restored_at = restored_at
    order.save(update_fields=["stock_restored_at"])
    return True


def _aggregate_fulfilment_status(order):
    statuses = list(order.fulfilments.values_list("status", flat=True))
    if not statuses:
        return order.status
    if len(set(statuses)) == 1:
        return statuses[0]
    if "disputed" in statuses:
        return "disputed"
    if any(value in {"cancelled", "refunded", "partially_fulfilled"} for value in statuses):
        return "partially_fulfilled"
    progression = ["pending", "paid", "accepted", "packed", "dispatched", "delivered", "completed"]
    return min(statuses, key=lambda value: progression.index(value) if value in progression else len(progression))


@transaction.atomic
def transition_order(order_id, actor, next_status, reason="", metadata=None, system=False):
    order = Order.objects.select_for_update().get(id=order_id)
    role = "system" if system else actor_role(order, actor)
    allowed = {"seller": SELLER_TRANSITIONS, "transporter": TRANSPORTER_TRANSITIONS, "buyer": BUYER_TRANSITIONS, "admin": ADMIN_TRANSITIONS, "system": {"pending": {"paid", "cancelled"}}}[role]
    reason = str(reason or "").strip()
    if next_status in {"cancelled", "failed_delivery", "disputed", "refunded"} and len(reason) < 5: raise ValidationError({"reason": "Provide a reason of at least five characters."})
    if role == "seller":
        fulfilment = OrderFulfilment.objects.select_for_update().filter(order=order, seller=actor).first()
        if fulfilment is None:
            owned_items = order.items.filter(listing__seller=actor)
            subtotal = sum((item.unit_price * item.quantity for item in owned_items), start=0)
            fulfilment = OrderFulfilment.objects.create(
                order=order, seller=actor, status=order.status, subtotal=subtotal,
                acceptance_deadline=order.acceptance_deadline,
            )
            owned_items.update(fulfilment=fulfilment)
        if next_status not in allowed.get(fulfilment.status, set()):
            raise ValidationError({"status": f"Seller cannot change {fulfilment.status} to {next_status}."})
        if fulfilment.status == "paid" and fulfilment.acceptance_deadline and timezone.now() > fulfilment.acceptance_deadline:
            raise ValidationError({"status": "The seller acceptance deadline has passed."})
        previous = fulfilment.status
        fulfilment.status = next_status
        fields = ["status", "updated_at"]
        if next_status == "cancelled":
            fulfilment.cancellation_reason = reason
            fields.append("cancellation_reason")
        fulfilment.save(update_fields=fields)
        if next_status == "cancelled":
            restore_fulfilment_stock(fulfilment)
        order.status = _aggregate_fulfilment_status(order)
        order.save(update_fields=["status"])
        event_metadata = {"fulfilment_id": fulfilment.id, "seller_id": actor.id, **(metadata or {})}
        OrderStatusHistory.objects.create(order=order, from_status=previous, to_status=next_status, actor=actor, reason=reason, metadata=event_metadata)
        AuditLog.objects.create(actor=actor, action=f"fulfilment.{next_status}", target_type="order_fulfilment", target_id=str(fulfilment.id), metadata={"reason": reason, "before": {"status": previous}, "after": {"status": next_status}, **event_metadata})
        Notification.objects.create(user=order.buyer, type="order", title=f"Order #{order.id} updated", message=f"{actor.username}'s fulfilment changed from {previous.replace('_', ' ')} to {next_status.replace('_', ' ')}.", action_url=f"/app/orders/{order.id}")
        deliver_order_update(order.buyer, order, previous, next_status)
        return order
    if next_status not in allowed.get(order.status, set()): raise ValidationError({"status": f"{role.title()} cannot change {order.status} to {next_status}."})
    if next_status in {"delivered", "failed_delivery"} and not order.delivery_evidence.filter(evidence_type="delivery" if next_status == "delivered" else "failed_delivery").exists():
        raise ValidationError({"evidence": "Delivery evidence is required for this transition."})
    previous = order.status
    order.status = next_status
    fields = ["status"]
    if next_status == "paid":
        order.acceptance_deadline = timezone.now() + timedelta(hours=24)
        order.payment_expires_at = None
        fields.extend(["acceptance_deadline", "payment_expires_at"])
    if next_status == "cancelled": order.cancellation_reason = reason; fields.append("cancellation_reason")
    order.save(update_fields=fields)
    if next_status == "paid":
        order.fulfilments.update(status="paid", acceptance_deadline=order.acceptance_deadline)
    if next_status == "cancelled":
        restore_order_stock(order)
        order.fulfilments.exclude(status__in=["cancelled", "refunded"]).update(status="cancelled", cancellation_reason=reason)
    if next_status == "completed":
        order.items.update(fulfilled_quantity=__import__("django.db.models", fromlist=["F"]).F("quantity"))
        order.fulfilments.exclude(status__in=["cancelled", "refunded"]).update(status="completed")
        from .finance import release_order_settlements
        release_order_settlements(order)
    OrderStatusHistory.objects.create(order=order, from_status=previous, to_status=next_status, actor=actor, reason=reason, metadata=metadata or {})
    AuditLog.objects.create(actor=actor, action=f"order.{next_status}", target_type="order", target_id=str(order.id), metadata={"reason": reason, "before": {"status": previous}, "after": {"status": next_status}, **(metadata or {})})
    recipients = {order.buyer_id}
    recipients.update(order.items.values_list("listing__seller_id", flat=True))
    if hasattr(order, "delivery") and order.delivery.transporter_id: recipients.add(order.delivery.transporter_id)
    Notification.objects.bulk_create([Notification(user_id=user_id, type="order", title=f"Order #{order.id} updated", message=f"Order status changed from {previous.replace('_', ' ')} to {next_status.replace('_', ' ')}.", action_url=f"/app/orders/{order.id}") for user_id in recipients if user_id != getattr(actor, "id", None)])
    for recipient in order.buyer.__class__.objects.filter(id__in=recipients).exclude(id=getattr(actor, "id", None)):
        deliver_order_update(recipient, order, previous, next_status)
    return order
