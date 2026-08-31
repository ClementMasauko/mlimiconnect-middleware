import json
import logging
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import get_connection, send_mail

from .models import MessageDelivery, NotificationPreference

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
    email = deliver_email(user, subject, email_message, category, essential=True)
    sms = deliver_sms(user, sms_message, category, essential=True)
    return email, sms


def deliver_order_update(user, order, previous, current):
    message = f"MlimiConnect: Order #{order.id} changed from {previous.replace('_', ' ')} to {current.replace('_', ' ')}."
    deliver_email(user, f"Order #{order.id} updated", message, "orders")
    deliver_sms(user, message, "orders")
