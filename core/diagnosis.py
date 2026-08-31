import base64
import hashlib
import io
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError
from django.conf import settings


SUPPORTED_CROPS = [
    "apple", "banana", "barley", "cassava", "citrus", "cocoa", "coffee", "corn (maize)",
    "cotton", "cucumber", "eggplant", "garlic", "grapevine", "oil palm", "onion", "potato",
    "rice", "soybean", "sugarcane", "tea", "tobacco", "tomato", "wheat",
]
CONSENT_VERSION = "2026-08-30"
DISCLAIMER = "This is an automated list of possibilities, not a confirmed diagnosis. Consult a qualified agricultural extension worker before treatment."


class DiagnosisError(RuntimeError):
    pass


def prepare_image(upload):
    if not upload or upload.size <= 0 or upload.size > settings.DIAGNOSIS_MAX_IMAGE_BYTES:
        raise DiagnosisError("Choose an image no larger than 8 MB.")
    raw = upload.read()
    signatures = ((b"\xff\xd8\xff", "JPEG"), (b"\x89PNG\r\n\x1a\n", "PNG"), (b"RIFF", "WEBP"))
    expected = next((fmt for signature, fmt in signatures if raw.startswith(signature)), None)
    if not expected or (expected == "WEBP" and raw[8:12] != b"WEBP"):
        raise DiagnosisError("The file signature is not a supported JPG, PNG or WebP image.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != expected:
                raise DiagnosisError("The image content does not match its file type.")
            width, height = source.size
            if width < 200 or height < 200 or width > 6000 or height > 6000:
                raise DiagnosisError("Image dimensions must be between 200 and 6,000 pixels on each side.")
            image = source.convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            # Re-encoding without EXIF, ICC, comments or GPS strips metadata for every upload.
            image.save(output, format="JPEG", quality=84, optimize=True)
            cleaned = output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise DiagnosisError("The uploaded image is invalid or unsafe to process.") from exc
    return cleaned, hashlib.sha256(cleaned).hexdigest()


def _request(method, path, payload=None):
    if settings.DIAGNOSIS_PROVIDER != "kindwise_crop_health" or not settings.DIAGNOSIS_API_KEY:
        raise DiagnosisError("Automated crop diagnosis is not configured yet.")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{settings.DIAGNOSIS_API_URL}{path}", data=body, method=method,
        headers={"Api-Key": settings.DIAGNOSIS_API_KEY, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=settings.DIAGNOSIS_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        raise DiagnosisError(f"Diagnosis provider rejected the request ({exc.code}).") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DiagnosisError("Diagnosis provider is temporarily unavailable.") from exc


def identify(cleaned_image):
    query = urlencode({"details": "common_names,type,severity,symptoms,wiki_url"})
    payload = _request("POST", f"/identification?{query}", {"images": [base64.b64encode(cleaned_image).decode("ascii")]})
    result = payload.get("result") or {}
    def top_three(kind):
        suggestions = ((result.get(kind) or {}).get("suggestions") or [])[:3]
        return [{
            "name": str(item.get("name") or item.get("common_name") or "Unknown")[:160],
            "scientific_name": str(item.get("scientific_name") or "")[:160],
            "confidence": round(float(item.get("probability") or 0) * 100, 1),
            "type": str(((item.get("details") or {}).get("type") or ""))[:80],
            "severity": str(((item.get("details") or {}).get("severity") or ""))[:80],
        } for item in suggestions]
    safe_result = {"crops": top_three("crop"), "possibilities": top_three("disease"), "warning": DISCLAIMER}
    reference = str(payload.get("access_token") or payload.get("id") or payload.get("custom_id") or "")[:160]
    return reference, safe_result


def delete_remote(reference):
    if not reference:
        return True
    _request("DELETE", f"/identification/{reference}")
    return True
