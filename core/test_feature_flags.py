from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.models import User


@override_settings(
    FEATURE_AUCTIONS_ENABLED=False,
    FEATURE_SUBSCRIPTIONS_ENABLED=False,
    FEATURE_EXPERT_REQUESTS_ENABLED=False,
    FEATURE_PROMOTIONS_ENABLED=False,
)
class DisabledCommercialFeatureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="feature-user", email="feature@example.invalid", password="pass-123", can_sell=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_auction_listing_is_rejected(self):
        response = self.client.post("/api/marketplace/listings/", {
            "name": "Auction lot", "description": "Not active", "price": "100",
            "quantity": 1, "category": "produce", "listing_type": "auction",
        })
        self.assertEqual(response.status_code, 400)

    def test_subscription_and_expert_requests_are_unavailable(self):
        self.assertEqual(self.client.get("/api/subscriptions/me/").status_code, 503)
        self.assertEqual(self.client.post("/api/subscriptions/checkout-sessions/", {}).status_code, 503)
        self.assertEqual(self.client.post("/api/advisory/expert-consultations/", {}).status_code, 503)
