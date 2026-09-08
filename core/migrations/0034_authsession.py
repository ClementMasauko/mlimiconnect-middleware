import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0033_user_two_factor_authentication")]
    operations = [migrations.CreateModel(name="AuthSession", fields=[
        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
        ("session_key", models.CharField(max_length=40, unique=True)),
        ("user_agent", models.CharField(blank=True, max_length=300)),
        ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
        ("created_at", models.DateTimeField(auto_now_add=True)),
        ("last_seen_at", models.DateTimeField(auto_now=True)),
        ("revoked_at", models.DateTimeField(blank=True, null=True)),
        ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="auth_sessions", to="core.user")),
    ])]
