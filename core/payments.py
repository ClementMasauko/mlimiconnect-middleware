import json
import secrets
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Order, PaymentReconciliation
from .order_lifecycle import transition_order


class PaymentProviderError(RuntimeError):
    pass


def _provider_request(url, *, method="GET", payload=None):
    if not settings.PAYCHANGU_SECRET_KEY:
        raise PaymentProviderError("PayChangu test credentials are not configured.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=body, method=method, headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.PAYCHANGU_SECRET_KEY}",
        "User-Agent": "MlimiConnect/1.0",
    })
    try:
        with urlopen(request, timeout=settings.PAYCHANGU_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        # Provider response bodies can contain customer/payment information and
        # are deliberately not included in application errors or logs.
        raise PaymentProviderError(f"PayChangu rejected the request ({error.code}).") from error
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
        raise PaymentProviderError("PayChangu is temporarily unavailable.") from error
    if not isinstance(result, dict):
        raise PaymentProviderError("PayChangu returned an invalid response.")
    return result


def create_transaction_reference(order):
    return f"MC-{order.id}-{secrets.token_hex(6).upper()}"


def initialize_checkout(order):
    tx_ref = create_transaction_reference(order)
    frontend = settings.FRONTEND_URL.rstrip("/")
    payload = {
        "amount": format(order.total, "f"),
        "currency": settings.PAYMENT_CURRENCY,
        "email": order.buyer.email,
        "first_name": order.buyer.first_name or order.buyer.username,
        "last_name": order.buyer.last_name or "Customer",
        "callback_url": f"{frontend}/app/orders/{order.id}?payment=processing",
        "return_url": f"{frontend}/app/checkout?payment=cancelled",
        "tx_ref": tx_ref,
        "customization": {"title": f"MlimiConnect order #{order.id}", "description": "Agricultural marketplace order"},
        "meta": {"order_id": order.id, "payment_method": order.payment_method},
    }
    result = _provider_request(f"{settings.PAYCHANGU_API_URL}/payment", method="POST", payload=payload)
    checkout_url = ((result.get("data") or {}).get("checkout_url") or result.get("checkout_url"))
    if not isinstance(checkout_url, str) or not checkout_url.startswith("https://"):
        raise PaymentProviderError("PayChangu did not return a secure checkout URL.")
    order.provider_reference = tx_ref
    order.save(update_fields=["provider_reference"])
    PaymentReconciliation.objects.create(
        order=order, provider="paychangu", provider_reference=tx_ref,
        expected_amount=order.total, status="pending",
        provider_payload={"mode": settings.PAYMENT_MODE, "created_at": timezone.now().isoformat()},
    )
    return checkout_url, tx_ref


def _verification_data(result):
    data = result.get("data")
    return data if isinstance(data, dict) else result


@transaction.atomic
def verify_and_reconcile(tx_ref):
    reconciliation = PaymentReconciliation.objects.select_for_update().select_related("order__buyer").filter(
        provider="paychangu", provider_reference=tx_ref,
    ).first()
    if not reconciliation:
        return None, "unknown_reference"
    if reconciliation.status == "matched" and reconciliation.order.status != "pending":
        return reconciliation.order, "already_processed"
    result = _provider_request(f"{settings.PAYCHANGU_API_URL}/verify-payment/{quote(tx_ref, safe='')}")
    data = _verification_data(result)
    try:
        amount = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError):
        amount = Decimal("-1")
    returned_ref = str(data.get("tx_ref") or data.get("reference") or "")
    provider_status = str(data.get("status") or "").casefold()
    currency = str(data.get("currency") or "").upper()
    mode = str(data.get("mode") or "").casefold()
    expected_mode = settings.PAYMENT_MODE.casefold()
    matched = (
        returned_ref == tx_ref and provider_status in {"success", "successful"}
        and currency == settings.PAYMENT_CURRENCY and amount >= reconciliation.expected_amount
        and (not mode or mode == expected_mode)
    )
    reconciliation.settled_amount = amount if amount >= 0 else None
    reconciliation.status = "matched" if matched else "mismatch"
    reconciliation.reconciled_at = timezone.now()
    authorization = data.get("authorization") if isinstance(data.get("authorization"), dict) else {}
    reconciliation.provider_payload = {
        "status": provider_status, "currency": currency, "amount": str(amount) if amount >= 0 else None,
        "mode": mode, "channel": str(authorization.get("channel") or "")[:40],
        "provider_reference": str(data.get("reference") or "")[:120],
        "verified_at": reconciliation.reconciled_at.isoformat(),
    }
    reconciliation.save(update_fields=["settled_amount", "status", "reconciled_at", "provider_payload"])
    if not matched:
        return reconciliation.order, "mismatch"
    order = reconciliation.order
    if order.status == "pending":
        order = transition_order(order.id, order.buyer, "paid", "Payment verified by PayChangu.", {"provider": "paychangu", "tx_ref": tx_ref}, system=True)
    return order, "matched"


def extract_transaction_reference(payload):
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return str(data.get("tx_ref") or payload.get("tx_ref") or "").strip()[:120]
