from django.db import migrations, models
import core.storage


class Migration(migrations.Migration):
    dependencies = [("core", "0017_messagedelivery")]
    operations = [
        migrations.AlterField(model_name="organizationdocument", name="file", field=models.FileField(storage=core.storage.AdaptiveCloudinaryStorage(protected=True), upload_to="organization-documents/%Y/%m/")),
        migrations.AlterField(model_name="traceabilityevidence", name="file", field=models.FileField(storage=core.storage.AdaptiveCloudinaryStorage(protected=True), upload_to="traceability-evidence/")),
        migrations.AlterField(model_name="transporterdocument", name="file", field=models.FileField(storage=core.storage.AdaptiveCloudinaryStorage(protected=True), upload_to="transporter-documents/%Y/%m/")),
        migrations.AlterField(model_name="deliveryevidence", name="file", field=models.FileField(blank=True, storage=core.storage.AdaptiveCloudinaryStorage(protected=True), upload_to="delivery-evidence/")),
    ]
