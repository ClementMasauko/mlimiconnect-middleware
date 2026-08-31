import hashlib
import hmac
import json
from unittest.mock import patch
from urllib.error import URLError

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Listing, Order, PaymentReconciliation, User


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps(self.payload).encode("utf-8")


SETTINGS = {
    "PAYMENTS_ENABLED": True, "PAYMENT_PROVIDER": "paychangu", "PAYMENT_MODE": "test",
    "PAYMENT_CURRENCY": "MWK", "PAYCHANGU_API_URL": "https://api.paychangu.com",
    "PAYCHANGU_SECRET_KEY": "test-secret", "PAYCHANGU_TIMEOUT_SECONDS": 2,
    "PAYMENT_WEBHOOK_SECRET": "webhook-secret", "FRONTEND_URL": "https://app.example.mw",
}


@override_settings(**SETTINGS)
class PayChanguTests(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(username="buyer", email="buyer@example.mw", password="pass-123456")
        self.seller = User.objects.create_user(username="seller", email="seller@example.mw", password="pass-123456", can_sell=True)
        self.listing = Listing.objects.create(seller=self.seller, name="Maize", description="Dry maize", price="1000", quantity=10, category="produce", approval_status="approved")
        self.client = APIClient()
        self.client.force_authenticate(self.buyer)

    @patch("core.payments.urlopen")
    def test_checkout_creates_provider_session_and_pending_reconciliation(self, mocked_open):
        mocked_open.return_value = FakeResponse({"status": "success", "data": {"checkout_url": "https://checkout.paychangu.com/session-1"}})
        response = self.client.post("/api/payments/checkout-sessions/", {"payment_method": "airtel_money", "items": [{"product_id": self.listing.id, "quantity": 2}]}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["checkout_url"], "https://checkout.paychangu.com/session-1")
        order = Order.objects.get(id=response.data["order"]["id"])
        self.assertTrue(order.provider_reference.startswith(f"MC-{order.id}-"))
        reconciliation = PaymentReconciliation.objects.get(order=order)
        self.assertEqual(reconciliation.status, "pending")
        sent = json.loads(mocked_open.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(sent["currency"], "MWK")
        self.assertEqual(sent["tx_ref"], order.provider_reference)
        self.assertNotIn("secret", json.dumps(sent).lower())

    @patch("core.payments.urlopen", side_effect=URLError("offline"))
    def test_provider_failure_rolls_back_order_and_stock_reservation(self, _mocked_open):
        response = self.client.post("/api/payments/checkout-sessions/", {"payment_method": "card", "items": [{"product_id": self.listing.id, "quantity": 2}]}, format="json")
        self.assertEqual(response.status_code, 502)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.quantity, 10)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(PaymentReconciliation.objects.count(), 0)

    @patch("core.payments.urlopen")
    def test_verified_webhook_marks_order_paid_once(self, mocked_open):
        order = Order.objects.create(buyer=self.buyer, status="pending", subtotal="2000", total="2000", payment_method="airtel_money", provider_reference="MC-1-ABC")
        order.items.create(listing=self.listing, quantity=2, unit_price="1000")
        PaymentReconciliation.objects.create(order=order, provider="paychangu", provider_reference="MC-1-ABC", expected_amount="2000")
        mocked_open.return_value = FakeResponse({"status": "success", "data": {"tx_ref": "MC-1-ABC", "status": "success", "currency": "MWK", "amount": 2000, "mode": "test", "reference": "provider-123", "authorization": {"channel": "Mobile Money"}}})
        body = b'{"event_type":"checkout.payment","data":{"tx_ref":"MC-1-ABC"}}'
        signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
        first = self.client.generic("POST", "/api/payments/webhooks/paychangu/", body, content_type="application/json", HTTP_SIGNATURE=signature)
        second = self.client.generic("POST", "/api/payments/webhooks/paychangu/", body, content_type="application/json", HTTP_SIGNATURE=signature)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["outcome"], "matched")
        self.assertEqual(second.data["outcome"], "already_processed")
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.status_history.filter(to_status="paid").count(), 1)
        reconciliation = order.reconciliations.get()
        self.assertEqual(reconciliation.status, "matched")
        self.assertNotIn("customer", reconciliation.provider_payload)
