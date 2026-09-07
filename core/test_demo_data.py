from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Listing


class DemoDataCommandTests(TestCase):
    @override_settings(ALLOW_DEMO_DATA=False)
    def test_seed_is_denied_by_default(self):
        stderr = StringIO()

        call_command("seed_demo_listings", stderr=stderr)

        self.assertFalse(Listing.objects.filter(
            seller__username="mlimiconnect_demo_seller",
            is_active=True,
        ).exists())
        self.assertIn("Demo seeding is disabled", stderr.getvalue())

    @override_settings(ALLOW_DEMO_DATA=True)
    def test_seed_requires_explicit_sandbox_opt_in(self):
        call_command("seed_demo_listings", stdout=StringIO())

        self.assertEqual(
            Listing.objects.filter(
                seller__username="mlimiconnect_demo_seller",
                name__startswith="Demo ",
            ).count(),
            3,
        )
