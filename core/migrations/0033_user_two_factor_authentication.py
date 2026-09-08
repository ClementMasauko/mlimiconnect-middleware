import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0032_user_google_onboarding_completed")]
    operations = [
        migrations.AddField(model_name="user", name="two_factor_enabled", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="user", name="two_factor_secret", field=models.TextField(blank=True)),
        migrations.AddField(model_name="user", name="two_factor_pending_secret", field=models.TextField(blank=True)),
        migrations.AddField(model_name="user", name="two_factor_recovery_codes", field=models.JSONField(blank=True, default=list)),
        migrations.CreateModel(
            name="TwoFactorLoginChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("backend", models.CharField(default="django.contrib.auth.backends.ModelBackend", max_length=180)),
                ("expires_at", models.DateTimeField()),
                ("used", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="two_factor_login_challenges", to="core.user")),
            ],
        ),
    ]
