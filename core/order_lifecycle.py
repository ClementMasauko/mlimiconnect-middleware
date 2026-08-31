from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from .models import AuditLog, Notification, Order, OrderStatusHistory
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


@transaction.atomic
def transition_order(order_id, actor, next_status, reason="", metadata=None, system=False):
    order = Order.objects.select_for_update().get(id=order_id)
    role = "system" if system else actor_role(order, actor)
    allowed = {"seller": SELLER_TRANSITIONS, "transporter": TRANSPORTER_TRANSITIONS, "buyer": BUYER_TRANSITIONS, "admin": ADMIN_TRANSITIONS, "system": {"pending": {"paid"}}}[role]
    if next_status not in allowed.get(order.status, set()): raise ValidationError({"status": f"{role.title()} cannot change {order.status} to {next_status}."})
    reason = str(reason or "").strip()
    if next_status in {"cancelled", "failed_delivery", "disputed", "refunded"} and len(reason) < 5: raise ValidationError({"reason": "Provide a reason of at least five characters."})
    if role == "seller" and order.status == "paid" and order.acceptance_deadline and timezone.now() > order.acceptance_deadline: raise ValidationError({"status": "The seller acceptance deadline has passed."})
    if next_status in {"delivered", "failed_delivery"} and not order.delivery_evidence.filter(evidence_type="delivery" if next_status == "delivered" else "failed_delivery").exists():
        raise ValidationError({"evidence": "Delivery evidence is required for this transition."})
    previous = order.status
    order.status = next_status
    fields = ["status"]
    if next_status == "paid": order.acceptance_deadline = timezone.now() + timedelta(hours=24); fields.append("acceptance_deadline")
    if next_status == "cancelled": order.cancellation_reason = reason; fields.append("cancellation_reason")
    order.save(update_fields=fields)
    if next_status == "cancelled":
        for item in order.items.select_related("listing").select_for_update():
            item.listing.quantity += item.quantity
            item.listing.is_active = True
            item.listing.save(update_fields=["quantity", "is_active"])
    if next_status == "completed": order.items.update(fulfilled_quantity=__import__("django.db.models", fromlist=["F"]).F("quantity"))
    OrderStatusHistory.objects.create(order=order, from_status=previous, to_status=next_status, actor=actor, reason=reason, metadata=metadata or {})
    AuditLog.objects.create(actor=actor, action=f"order.{next_status}", target_type="order", target_id=str(order.id), metadata={"reason": reason, "before": {"status": previous}, "after": {"status": next_status}, **(metadata or {})})
    recipients = {order.buyer_id}
    recipients.update(order.items.values_list("listing__seller_id", flat=True))
    if hasattr(order, "delivery") and order.delivery.transporter_id: recipients.add(order.delivery.transporter_id)
    Notification.objects.bulk_create([Notification(user_id=user_id, type="order", title=f"Order #{order.id} updated", message=f"Order status changed from {previous.replace('_', ' ')} to {next_status.replace('_', ' ')}.", action_url=f"/app/orders/{order.id}") for user_id in recipients if user_id != getattr(actor, "id", None)])
    for recipient in order.buyer.__class__.objects.filter(id__in=recipients).exclude(id=getattr(actor, "id", None)):
        deliver_order_update(recipient, order, previous, next_status)
    return order
