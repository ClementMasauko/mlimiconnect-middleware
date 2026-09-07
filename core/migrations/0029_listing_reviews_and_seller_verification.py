import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def attach_existing_reviews(apps, schema_editor):
    OrderReview = apps.get_model("core", "OrderReview")
    for review in OrderReview.objects.filter(listing__isnull=True).select_related("order"):
        listing_id = review.order.items.order_by("id").values_list("listing_id", flat=True).first()
        if listing_id:
            review.listing_id = listing_id
            review.save(update_fields=["listing"])
        else:
            review.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0028_financial_ledger_settlements_and_payouts")]
    operations = [
        migrations.AddField(model_name="user", name="is_seller_verified", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="user", name="seller_verified_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="user", name="seller_verified_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="verified_sellers", to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name="orderreview", name="order", field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reviews", to="core.order")),
        migrations.AddField(model_name="orderreview", name="listing", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reviews", to="core.listing")),
        migrations.RunPython(attach_existing_reviews, migrations.RunPython.noop),
        migrations.AlterField(model_name="orderreview", name="listing", field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reviews", to="core.listing")),
        migrations.AddConstraint(model_name="orderreview", constraint=models.UniqueConstraint(fields=("order", "listing"), name="unique_order_listing_review")),
    ]
