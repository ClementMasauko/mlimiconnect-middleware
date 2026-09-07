import json
import logging
from datetime import timedelta
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import get_connection, send_mail
from django.db import transaction
from django.utils import timezone

from .models import MessageDelivery, NotificationPreference, OutboxMessage

logger = logging.getLogger("mlimiconnect")


def recipient_hint(value):
    value = str(value or "").strip()
    if "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:2]}***@{domain}"
    digits = "".join(character for character in value if character.isdigit())
    return f"***{digits[-4:]}" if digits else "unavailable"


def channel_enabled(user, channel, category, essential=False):
    if essential:
        return True
    key = f"{channel}{category.title()}"
    defaults = {"emailOrders": True, "smsOrders": False}
    preference = NotificationPreference.objects.filter(user=user).values_list("settings", flat=True).first() or {}
    return bool(preference.get(key, defaults.get(key, False)))


def deliver_email(user, subject, message, category, essential=False):
    if not user.email or not channel_enabled(user, "email", category, essential):
        return None
    delivery = MessageDelivery.objects.create(user=user, channel="email", category=category, provider="brevo", recipient_hint=recipient_hint(user.email))
    try:
        if settings.EMAIL_PROVIDER == "brevo_api":
            if not settings.BREVO_API_KEY:
                raise RuntimeError("Brevo API key is not configured")
            sender_name, sender_email = parseaddr(settings.DEFAULT_FROM_EMAIL)
            if not sender_email:
                raise RuntimeError("Default sender email is not configured")
            payload = {
                "sender": {"name": sender_name or "MlimiConnect", "email": sender_email},
                "to": [{"email": user.email}],
                "subject": subject,
                "textContent": message,
            }
            request = Request(
                settings.BREVO_API_URL,
                data=json.dumps(payload).encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json", "api-key": settings.BREVO_API_KEY, "User-Agent": "MlimiConnect/1.0"},
            )
            with urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                result = json.loads(response.read().decode("utf-8") or "{}")
            delivery.provider_reference = str(result.get("messageId", ""))[:120]
        else:
            connection = None
            if settings.EMAIL_BACKEND == "django.core.mail.backends.filebased.EmailBackend":
                connection = get_connection(file_path=settings.EMAIL_FILE_PATH)
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], connection=connection, fail_silently=False)
        delivery.status = "accepted"
    except Exception as error:
        delivery.status, delivery.error_code = "failed", type(error).__name__[:80]
        logger.warning("email_delivery_failed", extra={"category": category, "delivery_id": delivery.id, "error_code": delivery.error_code})
    delivery.save(update_fields=["status", "provider_reference", "error_code", "updated_at"])
    return delivery


def deliver_sms(user, message, category, essential=False):
    if not user.phone or not channel_enabled(user, "sms", category, essential):
        return None
    delivery = MessageDelivery.objects.create(user=user, channel="sms", category=category, provider="textbee", recipient_hint=recipient_hint(user.phone))
    if not settings.SMS_ENABLED or settings.SMS_PROVIDER != "textbee" or not settings.TEXTBEE_API_KEY:
        delivery.status, delivery.error_code = "skipped", "provider_not_configured"
        delivery.save(update_fields=["status", "error_code", "updated_at"])
        return delivery
    payload = {"recipients": [user.phone], "message": message}
    if settings.TEXTBEE_DEVICE_ID:
        payload["deviceId"] = settings.TEXTBEE_DEVICE_ID
    request = Request(
        f"{settings.TEXTBEE_API_URL}/gateway/send-sms",
        data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "MlimiConnect/1.0", "x-api-key": settings.TEXTBEE_API_KEY},
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        data = result.get("data", result)
        delivery.status = "accepted" if data.get("success") else "failed"
        delivery.provider_reference = str(data.get("smsBatchId", ""))[:120]
        if delivery.status == "failed": delivery.error_code = "provider_rejected"
    except HTTPError as error:
        delivery.status, delivery.error_code = "failed", f"http_{error.code}"
    except (URLError, TimeoutError, ValueError, OSError) as error:
        delivery.status, delivery.error_code = "failed", type(error).__name__[:80]
    if delivery.status == "failed":
        logger.warning("sms_delivery_failed", extra={"category": category, "delivery_id": delivery.id, "error_code": delivery.error_code})
    delivery.save(update_fields=["status", "provider_reference", "error_code", "updated_at"])
    return delivery


def deliver_security_code(user, subject, email_message, sms_message, category):
    email = enqueue_delivery(user, "email", category, email_message, subject=subject, essential=True)
    sms = enqueue_delivery(user, "sms", category, sms_message, essential=True)
    return email, sms


def deliver_order_update(user, order, previous, current):
    message = f"MlimiConnect: Order #{order.id} changed from {previous.replace('_', ' ')} to {current.replace('_', ' ')}."
    enqueue_delivery(user, "email", "orders", message, subject=f"Order #{order.id} updated")
    enqueue_delivery(user, "sms", "orders", message)


def enqueue_delivery(user, channel, category, body, subject="", essential=False):
    """Persist a message in the same transaction as the event that created it."""
    recipient = user.email if channel == "email" else user.phone
    if not recipient or not channel_enabled(user, channel, category, essential):
        return None
    message = OutboxMessage.objects.create(
        user=user,
        channel=channel,
        category=category,
        subject=subject,
        body=body,
        essential=essential,
    )
    if settings.OUTBOX_INLINE:
        process_outbox_message(message.id)
        message.refresh_from_db()
    return message


def process_outbox_message(message_id):
    now = timezone.now()
    with transaction.atomic():
        message = OutboxMessage.objects.select_for_update().select_related("user").filter(
            id=message_id,
            status__in=["pending", "processing"],
            available_at__lte=now,
        ).first()
        if not message:
            return False
        message.status = "processing"
        message.locked_at = now
        message.attempt_count += 1
        message.save(update_fields=["status", "locked_at", "attempt_count", "updated_at"])

    try:
        if not message.user:
            raise RuntimeError("recipient_deleted")
        delivery = (
            deliver_email(message.user, message.subject, message.body, message.category, message.essential)
            if message.channel == "email"
            else deliver_sms(message.user, message.body, message.category, message.essential)
        )
        if delivery is not None and delivery.status == "failed":
            raise RuntimeError(delivery.error_code or "provider_rejected")
    except Exception as error:
        message.last_error = type(error).__name__ if not str(error) else str(error)[:160]
        if message.attempt_count >= settings.OUTBOX_MAX_ATTEMPTS:
            message.status = "failed"
        else:
            message.status = "pending"
            message.available_at = now + timedelta(seconds=min(3600, 30 * (2 ** (message.attempt_count - 1))))
        message.locked_at = None
        message.save(update_fields=["status", "available_at", "locked_at", "last_error", "updated_at"])
        return False

    message.status = "sent"
    message.sent_at = timezone.now()
    message.locked_at = None
    message.last_error = ""
    message.save(update_fields=["status", "sent_at", "locked_at", "last_error", "updated_at"])
    return True


def process_pending_outbox(limit=100):
    now = timezone.now()
    stale_before = now - timedelta(minutes=10)
    OutboxMessage.objects.filter(status="processing", locked_at__lt=stale_before).update(status="pending", locked_at=None)
    message_ids = list(
        OutboxMessage.objects.filter(status="pending", available_at__lte=now)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    return sum(1 for message_id in message_ids if process_outbox_message(message_id))
