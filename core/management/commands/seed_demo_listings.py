from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import AuditLog, Listing, User, WholesalePriceTier


DEMO_LISTINGS = [
    {
        "name": "Demo Premium Maize - PayChangu Test",
        "description": "Approved demonstration listing for testing the MlimiConnect cart and PayChangu sandbox checkout. No real goods will be delivered.",
        "price": Decimal("2500.00"), "quantity": 80, "unit": "kg", "pack_size": Decimal("1"),
        "minimum_order": 1, "grade": "Grade 1", "variety": "Local white maize", "category": "maize",
        "storage_conditions": "Keep dry and protected from pests.", "delivery_radius_km": 40,
        "allow_partial_fulfilment": True,
    },
    {
        "name": "Demo Groundnuts - PayChangu Test",
        "description": "Approved demonstration listing for testing the MlimiConnect cart and PayChangu sandbox checkout. No real goods will be delivered.",
        "price": Decimal("4500.00"), "quantity": 45, "unit": "kg", "pack_size": Decimal("1"),
        "minimum_order": 1, "grade": "Standard", "variety": "CG7", "category": "groundnuts",
        "storage_conditions": "Store in a cool and dry place.", "delivery_radius_km": 40,
        "allow_partial_fulfilment": True,
    },
    {
        "name": "Demo Fresh Eggs - PayChangu Test",
        "description": "Approved demonstration listing for testing the MlimiConnect cart and PayChangu sandbox checkout. No real goods will be delivered.",
        "price": Decimal("6500.00"), "quantity": 30, "unit": "crate", "pack_size": Decimal("30"),
        "minimum_order": 1, "grade": "Table eggs", "variety": "Layer eggs", "category": "eggs",
        "storage_conditions": "Keep clean, cool and protected from breakage.", "delivery_radius_km": 25,
        "allow_partial_fulfilment": False,
    },
]


class Command(BaseCommand):
    help = "Create three clearly labelled, approved listings for sandbox checkout testing."

    def handle(self, *args, **options):
        seller, created = User.objects.get_or_create(
            username="mlimiconnect_demo_seller",
            defaults={
                "email": "demo-seller@mlimiconnect.invalid", "user_type": "farmer",
                "can_sell": True, "email_verified": True, "location": "Lilongwe",
            },
        )
        if created:
            seller.set_unusable_password()
            seller.save(update_fields=["password"])

        created_count = 0
        for values in DEMO_LISTINGS:
            name = values["name"]
            listing, was_created = Listing.objects.get_or_create(
                seller=seller,
                name=name,
                defaults={**values, "approval_status": "approved", "is_active": True},
            )
            if not was_created:
                continue
            created_count += 1
            WholesalePriceTier.objects.create(
                listing=listing,
                minimum_quantity=10,
                price_per_unit=(listing.price * Decimal("0.95")).quantize(Decimal("0.01")),
            )
            AuditLog.objects.create(
                actor=seller, action="listing.demo_seeded", target_type="listing", target_id=str(listing.id),
                metadata={"automated": True, "approved": True, "sandbox_only": True},
            )

        self.stdout.write(self.style.SUCCESS(
            f"Demo checkout listings ready: {created_count} created, {len(DEMO_LISTINGS) - created_count} already present."
        ))
