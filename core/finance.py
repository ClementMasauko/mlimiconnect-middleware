from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    LedgerAccount, LedgerPosting, LedgerTransaction, Payout,
    PlatformSetting, SellerSettlement, WalletTransaction,
)


MONEY = Decimal("0.01")


def _money(value):
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def _account(code, name, account_type, owner=None):
    account, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={"name": name, "account_type": account_type, "owner": owner},
    )
    return account


def seller_payable_account(seller):
    return _account(f"seller-payable:{seller.id}", f"Payable to {seller.username}", "liability", seller)


def processor_clearing_account():
    return _account("processor-clearing:mwk", "Payment processor clearing", "asset")


def commission_revenue_account():
    return _account("platform-commission:mwk", "Platform commission revenue", "revenue")


def refund_expense_account():
    return _account("refund-expense:mwk", "Customer refunds", "expense")


def unallocated_payment_account():
    return _account("unallocated-payments:mwk", "Unallocated customer payments", "liability")


@transaction.atomic
def post_transaction(*, reference, kind, postings, order=None, fulfilment=None, description="", metadata=None):
    existing = LedgerTransaction.objects.filter(reference=reference).first()
    if existing:
        return existing, False
    normalized = [(account, direction, _money(amount)) for account, direction, amount in postings]
    debit = sum((amount for _, direction, amount in normalized if direction == "debit"), Decimal("0"))
    credit = sum((amount for _, direction, amount in normalized if direction == "credit"), Decimal("0"))
    if debit <= 0 or debit != credit:
        raise ValueError(f"Ledger transaction is not balanced: debit={debit}, credit={credit}.")
    row = LedgerTransaction.objects.create(
        reference=reference, kind=kind, order=order, fulfilment=fulfilment,
        description=description, metadata=metadata or {},
    )
    LedgerPosting.objects.bulk_create([
        LedgerPosting(transaction=row, account=account, direction=direction, amount=amount)
        for account, direction, amount in normalized
    ])
    return row, True


def commission_percent():
    setting = PlatformSetting.objects.filter(key="fee_configuration").first()
    raw = (setting.value or {}).get("platform_percent", "0") if setting else "0"
    value = Decimal(str(raw))
    return min(max(value, Decimal("0")), Decimal("100"))


@transaction.atomic
def record_payment(order, provider_reference):
    percent = commission_percent()
    postings = [(processor_clearing_account(), "debit", order.total)]
    allocated = Decimal("0")
    for fulfilment in order.fulfilments.select_for_update().select_related("seller"):
        allocated += fulfilment.subtotal
        commission = _money(fulfilment.subtotal * percent / Decimal("100"))
        net = _money(fulfilment.subtotal - commission)
        SellerSettlement.objects.update_or_create(
            fulfilment=fulfilment,
            defaults={
                "seller": fulfilment.seller, "gross_amount": fulfilment.subtotal,
                "commission_amount": commission, "net_amount": net,
                "commission_percent": percent, "status": "pending",
            },
        )
        if net:
            postings.append((seller_payable_account(fulfilment.seller), "credit", net))
        if commission:
            postings.append((commission_revenue_account(), "credit", commission))
    remainder = _money(order.total - allocated)
    if remainder:
        postings.append((unallocated_payment_account(), "credit", remainder))
    return post_transaction(
        reference=f"payment:{provider_reference}", kind="payment", postings=postings,
        order=order, description=f"Payment received for order #{order.id}",
        metadata={"provider_reference": provider_reference, "commission_percent": str(percent)},
    )[0]


@transaction.atomic
def release_order_settlements(order):
    now = timezone.now()
    settlements = SellerSettlement.objects.select_for_update().filter(
        fulfilment__order=order, status="pending",
    )
    for settlement in settlements:
        settlement.status = "available"
        settlement.available_at = now
        settlement.save(update_fields=["status", "available_at", "updated_at"])
        WalletTransaction.objects.get_or_create(
            reference=f"settlement:{settlement.id}",
            defaults={
                "user": settlement.seller, "type": "sale", "amount": settlement.net_amount,
                "status": "completed", "metadata": {"settlement_id": settlement.id, "order_id": order.id},
            },
        )
    return settlements.count()


@transaction.atomic
def record_refund(refund):
    transaction_row = post_transaction(
        reference=f"refund:{refund.provider_reference}", kind="refund",
        postings=[
            (refund_expense_account(), "debit", refund.amount),
            (processor_clearing_account(), "credit", refund.amount),
        ],
        order=refund.order, description=f"Refund for order #{refund.order_id}",
        metadata={"refund_id": refund.id, "provider_reference": refund.provider_reference},
    )[0]
    if refund.amount >= refund.order.total:
        SellerSettlement.objects.filter(fulfilment__order=refund.order).exclude(status="paid").update(status="refunded")
    return transaction_row


@transaction.atomic
def create_payout(*, seller, settlements, provider, provider_reference, requested_by, destination_hint="", status="submitted", provider_payload=None, idempotency_key=None):
    if idempotency_key:
        existing = Payout.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            expected_ids = sorted(row.id for row in settlements)
            if existing.seller_id != seller.id or existing.provider_reference != provider_reference or sorted(existing.settlements.values_list("id", flat=True)) != expected_ids:
                raise ValueError("This idempotency key was already used for a different payout request.")
            return existing
    locked = list(SellerSettlement.objects.select_for_update().filter(
        id__in=[row.id for row in settlements], seller=seller, status="available",
    ))
    if not locked or len(locked) != len(settlements):
        raise ValueError("Every payout settlement must be available and belong to the seller.")
    amount = _money(sum((row.net_amount for row in locked), Decimal("0")))
    daily_total = Payout.objects.filter(seller=seller, created_at__date=timezone.localdate()).exclude(status__in=["failed", "cancelled"]).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if daily_total + amount > Decimal(str(settings.PAYOUT_DAILY_LIMIT_MWK)):
        raise ValueError("This payout would exceed the seller's daily payout limit.")
    if amount >= Decimal(str(settings.PAYOUT_DUAL_APPROVAL_THRESHOLD_MWK)):
        status = "requested"
    payout = Payout.objects.create(
        seller=seller, amount=amount, status=status, provider=provider,
        provider_reference=provider_reference, destination_hint=destination_hint,
        requested_by=requested_by, provider_payload=provider_payload or {}, idempotency_key=idempotency_key,
        paid_at=timezone.now() if status == "paid" else None,
    )
    payout.settlements.add(*locked)
    SellerSettlement.objects.filter(id__in=[row.id for row in locked]).update(status="held")
    if status == "paid":
        post_transaction(
            reference=f"payout:{provider_reference}", kind="payout",
            postings=[
                (seller_payable_account(seller), "debit", amount),
                (processor_clearing_account(), "credit", amount),
            ],
            description=f"Payout to {seller.username}", metadata={"payout_id": payout.id},
        )
        SellerSettlement.objects.filter(id__in=[row.id for row in locked]).update(status="paid")
        WalletTransaction.objects.create(
            user=seller, type="withdrawal", amount=-amount, status="completed",
            reference=f"payout:{provider_reference}", metadata={"payout_id": payout.id},
        )
    return payout


@transaction.atomic
def review_payout(*, payout, reviewer, decision, reason):
    payout = Payout.objects.select_for_update().get(id=payout.id)
    if payout.status != "requested":
        raise ValueError("Only requested payouts can be reviewed.")
    if payout.requested_by_id == reviewer.id:
        raise ValueError("A different administrator must review this payout.")
    if decision not in ["approve", "reject"]:
        raise ValueError("Decision must be approve or reject.")
    if len(reason.strip()) < 10:
        raise ValueError("An approval reason of at least 10 characters is required.")
    payout.approved_by = reviewer
    payout.approved_at = timezone.now()
    payout.approval_reason = reason.strip()
    if decision == "reject":
        payout.status = "cancelled"
        payout.save(update_fields=["approved_by", "approved_at", "approval_reason", "status", "updated_at"])
        payout.settlements.filter(status="held").update(status="available")
        return payout
    payout.status = "paid"
    payout.paid_at = timezone.now()
    payout.save(update_fields=["approved_by", "approved_at", "approval_reason", "status", "paid_at", "updated_at"])
    post_transaction(
        reference=f"payout:{payout.provider_reference}", kind="payout",
        postings=[(seller_payable_account(payout.seller), "debit", payout.amount), (processor_clearing_account(), "credit", payout.amount)],
        description=f"Payout to {payout.seller.username}", metadata={"payout_id": payout.id, "approved_by": reviewer.id},
    )
    payout.settlements.filter(status="held").update(status="paid")
    WalletTransaction.objects.create(user=payout.seller, type="withdrawal", amount=-payout.amount, status="completed", reference=f"payout:{payout.provider_reference}", metadata={"payout_id": payout.id})
    return payout
