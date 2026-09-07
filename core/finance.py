from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
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
def create_payout(*, seller, settlements, provider, provider_reference, requested_by, destination_hint="", status="submitted", provider_payload=None):
    locked = list(SellerSettlement.objects.select_for_update().filter(
        id__in=[row.id for row in settlements], seller=seller, status="available",
    ))
    if not locked or len(locked) != len(settlements):
        raise ValueError("Every payout settlement must be available and belong to the seller.")
    amount = _money(sum((row.net_amount for row in locked), Decimal("0")))
    payout = Payout.objects.create(
        seller=seller, amount=amount, status=status, provider=provider,
        provider_reference=provider_reference, destination_hint=destination_hint,
        requested_by=requested_by, provider_payload=provider_payload or {},
        paid_at=timezone.now() if status == "paid" else None,
    )
    payout.settlements.add(*locked)
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
