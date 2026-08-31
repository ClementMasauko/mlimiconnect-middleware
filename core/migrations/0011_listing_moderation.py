from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def approve_existing(apps, schema_editor):
    apps.get_model("core", "Listing").objects.update(approval_status="approved")


class Migration(migrations.Migration):
    dependencies = [("core", "0010_traceability_integrity")]
    operations = [
        migrations.AddField(model_name="listing", name="approval_status", field=models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("suspended", "Suspended")], default="pending", max_length=16)),
        migrations.AddField(model_name="listing", name="moderation_reason", field=models.TextField(blank=True)),
        migrations.AddField(model_name="listing", name="moderated_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="listing", name="moderated_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="moderated_listings", to=settings.AUTH_USER_MODEL)),
        migrations.RunPython(approve_existing, migrations.RunPython.noop),
    ]
