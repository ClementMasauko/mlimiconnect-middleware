from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_fulfilments(apps, schema_editor):
    Order = apps.get_model("core", "Order")
    OrderFulfilment = apps.get_model("core", "OrderFulfilment")
    for order in Order.objects.prefetch_related("items__listing"):
        subtotals = {}
        for item in order.items.all():
            seller_id = item.listing.seller_id
            subtotals[seller_id] = subtotals.get(seller_id, Decimal("0")) + item.unit_price * item.quantity
        for seller_id, subtotal in subtotals.items():
            fulfilment = OrderFulfilment.objects.create(
                order_id=order.id,
                seller_id=seller_id,
                status=order.status,
                subtotal=subtotal,
                acceptance_deadline=order.acceptance_deadline,
                cancellation_reason=order.cancellation_reason,
                stock_restored_at=order.stock_restored_at if order.status == "cancelled" else None,
            )
            order.items.filter(listing__seller_id=seller_id).update(fulfilment_id=fulfilment.id)


class Migration(migrations.Migration):
    dependencies = [("core", "0026_deactivate_demo_checkout_listings")]
    operations = [
        migrations.AddField(
            model_name="order",
            name="payment_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="order",
            name="stock_restored_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="OrderFulfilment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("pending", "Pending"), ("paid", "Paid"), ("accepted", "Accepted"), ("packed", "Packed"), ("dispatched", "Dispatched"), ("delivered", "Delivered"), ("completed", "Completed"), ("partially_fulfilled", "Partially Fulfilled"), ("failed_delivery", "Failed Delivery"), ("disputed", "Disputed"), ("cancelled", "Cancelled"), ("refunded", "Refunded"), ("fulfilled", "Fulfilled")], default="pending", max_length=20)),
                ("subtotal", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14)),
                ("acceptance_deadline", models.DateTimeField(blank=True, null=True)),
                ("cancellation_reason", models.TextField(blank=True)),
                ("stock_restored_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="fulfilments", to="core.order")),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_fulfilments", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name="orderitem",
            name="fulfilment",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="items", to="core.orderfulfilment"),
        ),
        migrations.AddConstraint(
            model_name="orderfulfilment",
            constraint=models.UniqueConstraint(fields=("order", "seller"), name="unique_order_fulfilment_seller"),
        ),
        migrations.RunPython(backfill_fulfilments, migrations.RunPython.noop),
    ]
