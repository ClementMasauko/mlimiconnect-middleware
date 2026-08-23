import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("core", "0004_passwordresetrequest")]
    operations = [migrations.CreateModel(name="AccountDeletionRequest", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)), ("code_hash", models.CharField(max_length=128)), ("expires_at", models.DateTimeField()), ("used", models.BooleanField(default=False)), ("created_at", models.DateTimeField(auto_now_add=True)), ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="account_deletion_requests", to=settings.AUTH_USER_MODEL))])]
