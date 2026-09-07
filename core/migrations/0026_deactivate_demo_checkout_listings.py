from django.db import migrations


DEMO_USERNAME = "mlimiconnect_demo_seller"
DEMO_NAMES = [
    "Demo Premium Maize - PayChangu Test",
    "Demo Groundnuts - PayChangu Test",
    "Demo Fresh Eggs - PayChangu Test",
]


def deactivate_demo_checkout_listings(apps, schema_editor):
    Listing = apps.get_model("core", "Listing")
    Listing.objects.filter(
        seller__username=DEMO_USERNAME,
        name__in=DEMO_NAMES,
    ).update(
        is_active=False,
        approval_status="suspended",
        moderation_reason="Automated demo listing retired before production launch.",
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0025_seed_sandbox_checkout_listings")]
    operations = [
        migrations.RunPython(
            deactivate_demo_checkout_listings,
            migrations.RunPython.noop,
        )
    ]
