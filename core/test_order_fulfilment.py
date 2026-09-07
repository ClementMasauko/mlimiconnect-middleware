from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Listing, Order
from core.order_expiry import expire_pending_orders
from core.order_lifecycle import transition_order
from core.serializers import CheckoutSerializer
from core.models import User


@override_settings(PAYMENT_PENDING_TTL_MINUTES=30)
class SellerFulfilmentTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="buyer-split", email="buyer-split@example.invalid", password="test-pass-123", can_buy=True)
        self.seller_a = User.objects.create_user(username="seller-a", email="seller-a@example.invalid", password="test-pass-123", can_sell=True)
        self.seller_b = User.objects.create_user(username="seller-b", email="seller-b@example.invalid", password="test-pass-123", can_sell=True)
        self.listing_a = Listing.objects.create(seller=self.seller_a, name="Maize A", description="A", price=100, quantity=10, category="maize", approval_status="approved")
        self.listing_b = Listing.objects.create(seller=self.seller_b, name="Beans B", description="B", price=200, quantity=10, category="beans", approval_status="approved")

    def checkout(self):
        serializer = CheckoutSerializer(data={
            "payment_method": "card",
            "items": [
                {"product_id": self.listing_a.id, "quantity": 2},
                {"product_id": self.listing_b.id, "quantity": 3},
            ],
        }, context={"request": SimpleNamespace(user=self.buyer)})
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    def test_checkout_creates_one_fulfilment_per_seller(self):
        order = self.checkout()
        rows = order.fulfilments.order_by("seller__username")
        self.assertEqual(rows.count(), 2)
        self.assertEqual(list(rows.values_list("subtotal", flat=True)), [200, 600])
        self.assertTrue(all(order.items.filter(fulfilment=row, listing__seller=row.seller).exists() for row in rows))
        self.assertIsNotNone(order.payment_expires_at)

    def test_one_seller_cannot_advance_another_sellers_fulfilment(self):
        order = self.checkout()
        transition_order(order.id, self.buyer, "paid", system=True, reason="Payment verified.")
        transition_order(order.id, self.seller_a, "accepted")
        statuses = dict(order.fulfilments.values_list("seller__username", "status"))
        self.assertEqual(statuses, {"seller-a": "accepted", "seller-b": "paid"})

    def test_expiry_restores_stock_only_once(self):
        order = self.checkout()
        Order.objects.filter(id=order.id).update(payment_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(expire_pending_orders(), 1)
        self.assertEqual(expire_pending_orders(), 0)
        self.listing_a.refresh_from_db(); self.listing_b.refresh_from_db(); order.refresh_from_db()
        self.assertEqual((self.listing_a.quantity, self.listing_b.quantity), (10, 10))
        self.assertEqual(order.status, "cancelled")
        self.assertIsNotNone(order.stock_restored_at)
