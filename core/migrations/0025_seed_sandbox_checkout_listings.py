from decimal import Decimal

from django.contrib.auth.hashers import make_password
from django.db import migrations


DEMO_LISTINGS = [
    ("Demo Premium Maize - PayChangu Test", "maize", Decimal("2500.00"), 80, "kg", Decimal("1"), "Grade 1", "Local white maize", 40, True),
    ("Demo Groundnuts - PayChangu Test", "groundnuts", Decimal("4500.00"), 45, "kg", Decimal("1"), "Standard", "CG7", 40, True),
    ("Demo Fresh Eggs - PayChangu Test", "eggs", Decimal("6500.00"), 30, "crate", Decimal("30"), "Table eggs", "Layer eggs", 25, False),
]


def seed_checkout_listings(apps, schema_editor):
    User = apps.get_model("core", "User")
    Listing = apps.get_model("core", "Listing")
    WholesalePriceTier = apps.get_model("core", "WholesalePriceTier")
    AuditLog = apps.get_model("core", "AuditLog")

    seller, _ = User.objects.get_or_create(
        username="mlimiconnect_demo_seller",
        defaults={
            "email": "demo-seller@mlimiconnect.invalid", "password": make_password(None),
            "user_type": "farmer", "can_sell": True, "email_verified": True, "location": "Lilongwe",
        },
    )
    description = "Approved demonstration listing for testing the MlimiConnect cart and PayChangu sandbox checkout. No real goods will be delivered."
    for name, category, price, quantity, unit, pack_size, grade, variety, radius, partial in DEMO_LISTINGS:
        listing, created = Listing.objects.get_or_create(
            seller=seller, name=name,
            defaults={
                "description": description, "price": price, "quantity": quantity, "unit": unit,
                "pack_size": pack_size, "minimum_order": 1, "grade": grade, "variety": variety,
                "category": category, "storage_conditions": "Keep clean, cool and dry.",
                "delivery_radius_km": radius, "allow_partial_fulfilment": partial,
                "approval_status": "approved", "is_active": True,
            },
        )
        if not created:
            continue
        WholesalePriceTier.objects.create(
            listing=listing, minimum_quantity=10,
            price_per_unit=(price * Decimal("0.95")).quantize(Decimal("0.01")),
        )
        AuditLog.objects.create(
            actor=seller, action="listing.demo_seeded", target_type="listing", target_id=str(listing.id),
            metadata={"automated": True, "approved": True, "sandbox_only": True},
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0024_animalrecord_acquisition_date_and_more")]
    operations = [migrations.RunPython(seed_checkout_listings, migrations.RunPython.noop)]
