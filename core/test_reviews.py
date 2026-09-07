from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from .models import Listing, Order, OrderItem, OrderReview, User
from .serializers import ListingSerializer


class ListingReviewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser("review-admin", "review-admin@example.com", "Strong-pass-123")
        self.buyer = User.objects.create_user("review-buyer", "review-buyer@example.com", "Strong-pass-123", can_buy=True)
        self.seller = User.objects.create_user(
            "review-seller",
            "review-seller@example.com",
            "Strong-pass-123",
            can_sell=True,
            is_buyer_verified=True,
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            name="Review maize",
            description="A real listing used to verify review aggregation.",
            price=Decimal("1200.00"),
            quantity=10,
            category="Crops",
            approval_status="approved",
        )
        self.order = Order.objects.create(
            buyer=self.buyer,
            status="completed",
            subtotal=Decimal("1200.00"),
            total=Decimal("1200.00"),
            payment_method="mobile_money",
        )
        OrderItem.objects.create(order=self.order, listing=self.listing, quantity=1, unit_price=self.listing.price)

    def test_review_is_listing_specific_and_updates_public_aggregate(self):
        self.client.force_authenticate(self.buyer)
        response = self.client.post(
            "/api/marketplace/order-reviews/",
            {"order": self.order.id, "listing": self.listing.id, "rating": 4, "comment": "Good produce."},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(OrderReview.objects.filter(order=self.order, listing=self.listing, reviewer=self.buyer).exists())
        data = ListingSerializer(self.listing).data
        self.assertEqual(data["rating"], 4.0)
        self.assertEqual(data["reviewsCount"], 1)

    def test_buyer_verification_does_not_imply_seller_verification(self):
        self.assertFalse(ListingSerializer(self.listing).data["seller_verified"])
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            f"/api/admin/sellers/{self.seller.id}/verification/",
            {"decision": "verified", "reason": "Identity and seller records checked."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.seller.refresh_from_db()
        self.assertTrue(self.seller.is_seller_verified)
        self.listing.refresh_from_db()
        self.assertTrue(ListingSerializer(self.listing).data["seller_verified"])

    def test_review_rejects_listing_not_in_order(self):
        other = Listing.objects.create(
            seller=self.seller,
            name="Other beans",
            description="Not purchased in this order.",
            price=Decimal("900.00"),
            quantity=4,
            category="Crops",
            approval_status="approved",
        )
        self.client.force_authenticate(self.buyer)
        response = self.client.post(
            "/api/marketplace/order-reviews/",
            {"order": self.order.id, "listing": other.id, "rating": 5},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OrderReview.objects.exists())
