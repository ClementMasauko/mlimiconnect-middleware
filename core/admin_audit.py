from datetime import date, datetime
from decimal import Decimal
from django.forms.models import model_to_dict
from .models import AuditLog


def json_value(value):
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    if hasattr(value, "pk"): return value.pk
    return value


def snapshot(instance, fields):
    raw = model_to_dict(instance, fields=fields)
    return {key: json_value(value) for key, value in raw.items()}


def audit_change(*, actor, action, target, before, after, reason="", extra=None):
    metadata = {"reason": reason, "before": before, "after": after}
    if extra: metadata.update(extra)
    return AuditLog.objects.create(actor=actor, action=action, target_type=target._meta.model_name, target_id=str(target.pk), metadata=metadata)
