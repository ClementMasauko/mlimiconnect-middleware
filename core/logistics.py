import json
from urllib.request import Request, urlopen
from django.conf import settings
from .providers import ProviderUnavailable, require_provider

def create_external_shipment(delivery):
    require_provider("logistics")
    endpoint = getattr(settings, "LOGISTICS_API_URL", "").rstrip("/")
    if not endpoint: raise ProviderUnavailable("The logistics provider URL is not configured.")
    payload = json.dumps({"order_id": delivery.order_id, "pickup": delivery.pickup_location, "destination": delivery.delivery_location, "fee": str(delivery.delivery_fee)}).encode()
    request = Request(f"{endpoint}/shipments", data=payload, headers={"Authorization": f"Bearer {settings.LOGISTICS_API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=10) as response: data = json.loads(response.read().decode())
    except Exception as error:
        raise ProviderUnavailable("The logistics provider did not accept the shipment.") from error
    reference = str(data.get("reference") or data.get("id") or "").strip()
    if not reference: raise ProviderUnavailable("The logistics provider returned no shipment reference.")
    return reference, data
