import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .models import GeocodingCache, GeocodingRequestState

ATTRIBUTION = "© OpenStreetMap contributors"
ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright"

class GeocodingError(RuntimeError): pass
class GeocodingRateLimited(GeocodingError): pass

def sign_selection(result):
    return signing.dumps({key: result[key] for key in ["label", "latitude", "longitude", "osm_reference"]}, salt="mlimiconnect.geocoding")

def read_selection(token):
    try: return signing.loads(str(token or ""), salt="mlimiconnect.geocoding", max_age=3600)
    except signing.BadSignature as exc: raise GeocodingError("Search for the address again and confirm one of the current results.") from exc

def normalize_query(value):
    query = " ".join(str(value or "").strip().split())
    if len(query) < 3 or len(query) > 160:
        raise GeocodingError("Enter an address or place name between 3 and 160 characters.")
    return query

@transaction.atomic
def reserve_public_request():
    state, _ = GeocodingRequestState.objects.select_for_update().get_or_create(key="nominatim")
    now = timezone.now()
    if state.last_requested_at and (now - state.last_requested_at).total_seconds() < 1:
        raise GeocodingRateLimited("Please wait a moment before searching again.")
    state.last_requested_at = now
    state.save(update_fields=["last_requested_at"])

def search_malawi(query, language="en"):
    if settings.GEOCODING_PROVIDER != "nominatim":
        raise GeocodingError("The address-search provider is not configured.")
    normalized = normalize_query(query)
    query_hash = hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()
    cached = GeocodingCache.objects.filter(query_hash=query_hash, expires_at__gt=timezone.now()).first()
    if cached: return cached.results, True
    reserve_public_request()
    params = urlencode({"q": normalized, "format": "jsonv2", "addressdetails": 1, "limit": 5, "countrycodes": "mw", "viewbox": "32.67,-9.36,35.92,-17.13", "bounded": 1, "accept-language": "ny,en" if str(language).lower().startswith("ny") else "en,ny"})
    request = Request(f"{settings.GEOCODING_API_URL}/search?{params}", headers={"User-Agent": f"MlimiConnect/1.0 ({settings.SUPPORT_EMAIL})", "Accept": "application/json"})
    try:
        with urlopen(request, timeout=settings.GEOCODING_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc: raise GeocodingError(f"Address provider rejected the request ({exc.code}).") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc: raise GeocodingError("Address search is temporarily unavailable.") from exc
    results = []
    for row in payload[:5] if isinstance(payload, list) else []:
        try: latitude, longitude = Decimal(str(row["lat"])), Decimal(str(row["lon"]))
        except (KeyError, InvalidOperation): continue
        if not (Decimal("-17.2") <= latitude <= Decimal("-9.2") and Decimal("32.5") <= longitude <= Decimal("36.1")): continue
        address = row.get("address") if isinstance(row.get("address"), dict) else {}
        results.append({"label": str(row.get("display_name") or "")[:300], "latitude": str(latitude), "longitude": str(longitude), "osm_reference": f"{str(row.get('osm_type') or '')[:1].upper()}{str(row.get('osm_id') or '')}"[:80], "type": str(row.get("type") or "")[:50], "district": str(address.get("state_district") or address.get("county") or "")[:100]})
    GeocodingCache.objects.update_or_create(query_hash=query_hash, defaults={"normalized_query": normalized, "results": results, "expires_at": timezone.now() + timedelta(seconds=settings.GEOCODING_CACHE_SECONDS)})
    return results, False
