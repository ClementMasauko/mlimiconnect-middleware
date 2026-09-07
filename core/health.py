from datetime import timedelta

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone

from .models import OutboxMessage


def liveness(_request):
    return JsonResponse({"status": "ok"})


def readiness(_request):
    checks = {"database": "ok", "outbox": "ok"}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        checks["database"] = "unavailable"

    oldest = OutboxMessage.objects.filter(status="pending").order_by("created_at").values_list("created_at", flat=True).first() if checks["database"] == "ok" else None
    if oldest and oldest < timezone.now() - timedelta(minutes=15):
        checks["outbox"] = "backlogged"
    ready = checks["database"] == "ok"
    return JsonResponse({"status": "ready" if ready else "unavailable", "checks": checks}, status=200 if ready else 503)
