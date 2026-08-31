from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0016_user_email_verified_emailverificationrequest")]
    operations = [
        migrations.CreateModel(
            name="MessageDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("email", "Email"), ("sms", "SMS")], max_length=10)),
                ("category", models.CharField(max_length=40)),
                ("provider", models.CharField(max_length=40)),
                ("recipient_hint", models.CharField(max_length=40)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("accepted", "Accepted"), ("delivered", "Delivered"), ("failed", "Failed"), ("skipped", "Skipped")], default="pending", max_length=16)),
                ("provider_reference", models.CharField(blank=True, max_length=120)),
                ("attempt_count", models.PositiveSmallIntegerField(default=1)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="message_deliveries", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
