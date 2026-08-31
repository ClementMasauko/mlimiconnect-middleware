import hashlib
import json
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import TraceabilityAudit, TraceabilityBatch, TraceabilityEvent, TraceabilityEvidence


def canonical_payload(event, evidence_hashes=None):
    return {"batch_id": event.batch_id, "actor_id": event.actor_id, "event_type": event.event_type, "stage": event.stage, "description": event.description, "location": event.location, "quantity": str(event.quantity), "unit": event.unit, "occurred_at": event.occurred_at.isoformat(), "corrects_id": event.corrects_id, "previous_hash": event.previous_hash, "evidence_sha256": sorted(evidence_hashes if evidence_hashes is not None else event.evidence.values_list("sha256", flat=True))}


def digest_payload(payload): return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@transaction.atomic
def append_event(*, batch_id, actor, event_type, stage, description, location, quantity, unit, uploads=(), corrects=None):
    batch = TraceabilityBatch.objects.select_for_update().get(id=batch_id, owner=actor)
    previous = batch.events.order_by("-occurred_at", "-id").first()
    upload_data = []
    for upload in uploads:
        content = upload.read(); upload.seek(0)
        upload_data.append((upload, hashlib.sha256(content).hexdigest()))
    normalized_quantity = Decimal(str(quantity)).quantize(Decimal("0.001"))
    event = TraceabilityEvent(batch=batch, actor=actor, event_type=event_type, stage=stage, description=description, location=location, quantity=normalized_quantity, unit=unit, corrects=corrects, previous_hash=previous.event_hash if previous else "", occurred_at=timezone.now(), event_hash="")
    event.event_hash = digest_payload(canonical_payload(event, [digest for _, digest in upload_data]))
    event.save()
    for upload, digest in upload_data:
        TraceabilityEvidence.objects.create(event=event, uploaded_by=actor, file=upload, original_name=upload.name[:180], content_type=getattr(upload, "content_type", "application/octet-stream")[:80], size=upload.size, sha256=digest)
    TraceabilityAudit.objects.create(batch=batch, event=event, actor=actor, action="event.corrected" if corrects else "event.appended", reason=description, after={"event_hash": event.event_hash, "corrects_id": event.corrects_id})
    batch.status = stage; batch.save(update_fields=["status", "updated_at"])
    return event


def verify_chain(batch):
    previous = ""
    for event in batch.events.prefetch_related("evidence").order_by("occurred_at", "id"):
        if event.previous_hash != previous or event.event_hash != digest_payload(canonical_payload(event)):
            return False, event.id
        previous = event.event_hash
    return True, None
