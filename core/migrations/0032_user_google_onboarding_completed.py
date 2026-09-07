from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0031_user_google_subject")]
    operations = [
        migrations.AddField(
            model_name="user",
            name="google_onboarding_completed",
            field=models.BooleanField(default=True),
        ),
    ]
