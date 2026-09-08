from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0034_authsession")]
    operations = [
        migrations.AddField(model_name="payout", name="approved_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="payout", name="approval_reason", field=models.TextField(blank=True)),
        migrations.AddField(model_name="payout", name="idempotency_key", field=models.CharField(blank=True, max_length=120, null=True, unique=True)),
        migrations.AddField(model_name="payout", name="approved_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approved_payouts", to=settings.AUTH_USER_MODEL)),
    ]
