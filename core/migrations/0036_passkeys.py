from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("core", "0035_payout_approval_controls")]
    operations = [
        migrations.CreateModel(name="PasskeyCredential", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("credential_id", models.CharField(max_length=1024, unique=True)), ("public_key", models.BinaryField()), ("sign_count", models.PositiveBigIntegerField(default=0)), ("name", models.CharField(default="Passkey", max_length=100)), ("transports", models.JSONField(blank=True, default=list)), ("device_type", models.CharField(blank=True, max_length=40)), ("backed_up", models.BooleanField(default=False)), ("last_used_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="passkeys", to=settings.AUTH_USER_MODEL))]),
        migrations.CreateModel(name="PasskeyChallenge", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)), ("purpose", models.CharField(choices=[("register", "Register"), ("authenticate", "Authenticate")], max_length=16)), ("challenge", models.BinaryField()), ("expires_at", models.DateTimeField()), ("used", models.BooleanField(default=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="passkey_challenges", to=settings.AUTH_USER_MODEL))]),
    ]
