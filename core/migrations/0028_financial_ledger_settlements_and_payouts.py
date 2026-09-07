import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0027_order_seller_fulfilments_and_payment_expiry")]
    operations = [
        migrations.CreateModel(
            name="LedgerAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=120, unique=True)),
                ("name", models.CharField(max_length=180)),
                ("account_type", models.CharField(choices=[("asset", "Asset"), ("liability", "Liability"), ("revenue", "Revenue"), ("expense", "Expense")], max_length=16)),
                ("currency", models.CharField(default="MWK", max_length=3)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_accounts", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="LedgerTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=160, unique=True)),
                ("kind", models.CharField(choices=[("payment", "Payment"), ("refund", "Refund"), ("settlement", "Settlement"), ("payout", "Payout"), ("reversal", "Reversal"), ("adjustment", "Adjustment")], max_length=20)),
                ("description", models.TextField(blank=True)),
                ("metadata", models.JSONField(default=dict)),
                ("posted_at", models.DateTimeField(auto_now_add=True)),
                ("fulfilment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_transactions", to="core.orderfulfilment")),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="ledger_transactions", to="core.order")),
                ("reversed_by", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reverses", to="core.ledgertransaction")),
            ],
        ),
        migrations.CreateModel(
            name="LedgerPosting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("direction", models.CharField(choices=[("debit", "Debit"), ("credit", "Credit")], max_length=6)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="postings", to="core.ledgeraccount")),
                ("transaction", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="postings", to="core.ledgertransaction")),
            ],
        ),
        migrations.AddConstraint(model_name="ledgerposting", constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="ledger_posting_amount_positive")),
        migrations.CreateModel(
            name="SellerSettlement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("gross_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("commission_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("net_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("commission_percent", models.DecimalField(decimal_places=3, max_digits=6)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("available", "Available"), ("paid", "Paid"), ("held", "Held"), ("refunded", "Refunded")], default="pending", max_length=16)),
                ("available_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("fulfilment", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="settlement", to="core.orderfulfilment")),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="settlements", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Payout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("status", models.CharField(choices=[("requested", "Requested"), ("submitted", "Submitted"), ("paid", "Paid"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="requested", max_length=16)),
                ("provider", models.CharField(max_length=40)),
                ("provider_reference", models.CharField(max_length=120, unique=True)),
                ("destination_hint", models.CharField(blank=True, max_length=40)),
                ("provider_payload", models.JSONField(default=dict)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="requested_payouts", to=settings.AUTH_USER_MODEL)),
                ("seller", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payouts", to=settings.AUTH_USER_MODEL)),
                ("settlements", models.ManyToManyField(related_name="payouts", to="core.sellersettlement")),
            ],
        ),
    ]
