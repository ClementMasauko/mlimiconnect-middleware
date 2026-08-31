import hashlib
import json
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def chain_existing_events(apps, schema_editor):
    Event = apps.get_model("core", "TraceabilityEvent")
    for batch_id in Event.objects.values_list("batch_id", flat=True).distinct():
        previous = ""
        for event in Event.objects.filter(batch_id=batch_id).order_by("occurred_at", "id"):
            payload = {"batch_id": batch_id, "actor_id": event.actor_id, "event_type": event.event_type, "stage": event.stage, "description": event.description, "location": event.location, "quantity": str(event.quantity), "unit": event.unit, "occurred_at": event.occurred_at.isoformat(), "corrects_id": event.corrects_id, "previous_hash": previous, "evidence_sha256": []}
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            Event.objects.filter(id=event.id).update(previous_hash=previous, event_hash=digest)
            previous = digest


class Migration(migrations.Migration):
    dependencies = [("core", "0009_order_lifecycle")]
    operations = [
        migrations.AddField(model_name="traceabilityevent", name="event_type", field=models.CharField(default="update", max_length=80), preserve_default=False),
        migrations.AddField(model_name="traceabilityevent", name="quantity", field=models.DecimalField(decimal_places=3, default=0, max_digits=14), preserve_default=False),
        migrations.AddField(model_name="traceabilityevent", name="unit", field=models.CharField(default="unit", max_length=24), preserve_default=False),
        migrations.AddField(model_name="traceabilityevent", name="verification_status", field=models.CharField(choices=[("pending", "Pending"), ("verified", "Verified"), ("rejected", "Rejected")], default="pending", max_length=16)),
        migrations.AddField(model_name="traceabilityevent", name="verified_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="traceabilityevent", name="verified_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="verified_traceability_events", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="traceabilityevent", name="corrects", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="corrections", to="core.traceabilityevent")),
        migrations.AddField(model_name="traceabilityevent", name="previous_hash", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="traceabilityevent", name="event_hash", field=models.CharField(blank=True, max_length=64, null=True, unique=True)),
        migrations.RunPython(chain_existing_events, migrations.RunPython.noop),
        migrations.AlterField(model_name="traceabilityevent", name="event_hash", field=models.CharField(max_length=64, unique=True)),
        migrations.CreateModel(name="TraceabilityEvidence", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("file", models.FileField(upload_to="traceability-evidence/")), ("original_name", models.CharField(max_length=180)), ("content_type", models.CharField(max_length=80)), ("size", models.PositiveIntegerField()), ("sha256", models.CharField(max_length=64)), ("created_at", models.DateTimeField(auto_now_add=True)), ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="evidence", to="core.traceabilityevent")), ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL))]),
        migrations.CreateModel(name="TraceabilityAudit", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("action", models.CharField(max_length=80)), ("reason", models.TextField(blank=True)), ("before", models.JSONField(default=dict)), ("after", models.JSONField(default=dict)), ("created_at", models.DateTimeField(auto_now_add=True)), ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)), ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_history", to="core.traceabilitybatch")), ("event", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="core.traceabilityevent"))]),
    ]
