from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0030_otp_hardening_and_transactional_outbox")]
    operations = [
        migrations.AddField(
            model_name="user",
            name="google_subject",
            field=models.CharField(blank=True, max_length=255, null=True, unique=True),
        ),
    ]
