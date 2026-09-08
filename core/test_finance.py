from types import SimpleNamespace

from django.test import TestCase, override_settings

from core.finance import create_payout, record_payment, record_refund, release_order_settlements, review_payout
from core.models import LedgerTransaction, Listing, PlatformSetting, Refund, User, WalletTransaction
from core.serializers import CheckoutSerializer


class FinancialLedgerTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="finance-admin", email="finance-admin@example.invalid", password="pass-123", is_staff=True)
        self.buyer = User.objects.create_user(username="finance-buyer", email="finance-buyer@example.invalid", password="pass-123", can_buy=True)
        self.seller_a = User.objects.create_user(username="finance-a", email="finance-a@example.invalid", password="pass-123", can_sell=True)
        self.seller_b = User.objects.create_user(username="finance-b", email="finance-b@example.invalid", password="pass-123", can_sell=True)
        a = Listing.objects.create(seller=self.seller_a, name="Ledger maize", description="A", price=100, quantity=10, category="maize", approval_status="approved")
        b = Listing.objects.create(seller=self.seller_b, name="Ledger beans", description="B", price=200, quantity=10, category="beans", approval_status="approved")
        serializer = CheckoutSerializer(data={"payment_method": "card", "items": [{"product_id": a.id, "quantity": 2}, {"product_id": b.id, "quantity": 3}]}, context={"request": SimpleNamespace(user=self.buyer)})
        serializer.is_valid(raise_exception=True)
        self.order = serializer.save()
        PlatformSetting.objects.update_or_create(key="fee_configuration", defaults={"value": {"platform_percent": "5"}})

    def assert_balanced(self, transaction_row):
        debit = sum(row.amount for row in transaction_row.postings.filter(direction="debit"))
        credit = sum(row.amount for row in transaction_row.postings.filter(direction="credit"))
        self.assertEqual(debit, credit)

    def test_payment_creates_balanced_entries_and_commission_settlements(self):
        row = record_payment(self.order, "provider-payment-1")
        self.assert_balanced(row)
        self.assertEqual(self.order.fulfilments.get(seller=self.seller_a).settlement.net_amount, 190)
        self.assertEqual(self.order.fulfilments.get(seller=self.seller_b).settlement.net_amount, 570)
        self.assertEqual(record_payment(self.order, "provider-payment-1").id, row.id)
        self.assertEqual(LedgerTransaction.objects.filter(reference="payment:provider-payment-1").count(), 1)

    def test_release_payout_and_refund_are_balanced(self):
        record_payment(self.order, "provider-payment-2")
        self.assertEqual(release_order_settlements(self.order), 2)
        settlement = self.order.fulfilments.get(seller=self.seller_a).settlement
        payout = create_payout(seller=self.seller_a, settlements=[settlement], provider="bank", provider_reference="payout-1", requested_by=self.admin, status="paid")
        self.assertEqual(payout.amount, 190)
        self.assert_balanced(LedgerTransaction.objects.get(reference="payout:payout-1"))
        refund = Refund.objects.create(order=self.order, amount=100, provider="test", provider_reference="refund-1", status="settled", reason="Customer refund", requested_by=self.admin)
        self.assert_balanced(record_refund(refund))

    @override_settings(PAYOUT_DUAL_APPROVAL_THRESHOLD_MWK="100", PAYOUT_DAILY_LIMIT_MWK="10000")
    def test_high_value_payout_is_held_for_a_different_admin(self):
        approver = User.objects.create_user(username="finance-approver", password="pass-123", is_staff=True)
        record_payment(self.order, "provider-payment-approval")
        release_order_settlements(self.order)
        settlement = self.order.fulfilments.get(seller=self.seller_a).settlement
        payout = create_payout(seller=self.seller_a, settlements=[settlement], provider="bank", provider_reference="payout-approval", requested_by=self.admin, status="paid", idempotency_key="approval-key")
        self.assertEqual(payout.status, "requested")
        settlement.refresh_from_db()
        self.assertEqual(settlement.status, "held")
        with self.assertRaisesMessage(ValueError, "different administrator"):
            review_payout(payout=payout, reviewer=self.admin, decision="approve", reason="Approved after review")
        payout = review_payout(payout=payout, reviewer=approver, decision="approve", reason="Approved after review")
        settlement.refresh_from_db()
        self.assertEqual(payout.status, "paid")
        self.assertEqual(settlement.status, "paid")
        self.assertTrue(WalletTransaction.objects.filter(reference="payout:payout-approval").exists())

    @override_settings(PAYOUT_DUAL_APPROVAL_THRESHOLD_MWK="100", PAYOUT_DAILY_LIMIT_MWK="10000")
    def test_rejected_payout_restores_settlement_and_idempotency_is_safe(self):
        approver = User.objects.create_user(username="finance-rejector", password="pass-123", is_staff=True)
        record_payment(self.order, "provider-payment-reject")
        release_order_settlements(self.order)
        settlement = self.order.fulfilments.get(seller=self.seller_a).settlement
        kwargs = dict(seller=self.seller_a, settlements=[settlement], provider="bank", provider_reference="payout-reject", requested_by=self.admin, status="paid", idempotency_key="reject-key")
        payout = create_payout(**kwargs)
        self.assertEqual(create_payout(**kwargs).id, payout.id)
        payout = review_payout(payout=payout, reviewer=approver, decision="reject", reason="Destination could not be verified")
        settlement.refresh_from_db()
        self.assertEqual(payout.status, "cancelled")
        self.assertEqual(settlement.status, "available")

    @override_settings(PAYOUT_DUAL_APPROVAL_THRESHOLD_MWK="999999", PAYOUT_DAILY_LIMIT_MWK="100")
    def test_daily_payout_limit_blocks_excess(self):
        record_payment(self.order, "provider-payment-limit")
        release_order_settlements(self.order)
        settlement = self.order.fulfilments.get(seller=self.seller_a).settlement
        with self.assertRaisesMessage(ValueError, "daily payout limit"):
            create_payout(seller=self.seller_a, settlements=[settlement], provider="bank", provider_reference="payout-limit", requested_by=self.admin, status="paid")
