from django.contrib.sessions.models import Session
from django.utils import timezone

from .communications import enqueue_delivery
from .models import AuthSession


def request_ip(request):
    return request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR") or None


def register_auth_session(request, user):
    if not request.session.session_key: request.session.save()
    row, created = AuthSession.objects.update_or_create(session_key=request.session.session_key, defaults={"user": user, "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300], "ip_address": request_ip(request), "revoked_at": None})
    if created:
        enqueue_delivery(user, "email", "security", f"A new sign-in to MlimiConnect was completed. Device: {row.user_agent or 'Unknown device'}. IP: {row.ip_address or 'Unknown'}. If this was not you, reset your password and revoke the session.", subject="New MlimiConnect sign-in", essential=True)
    return row


def revoke_auth_session(row):
    Session.objects.filter(session_key=row.session_key).delete()
    row.revoked_at = timezone.now(); row.save(update_fields=["revoked_at"])
