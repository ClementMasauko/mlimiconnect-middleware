from rest_framework.views import exception_handler

NY_MESSAGES = {
    "The request could not be completed.": "Pempholi silinathe kukwaniritsidwa.",
    "Authentication credentials were not provided.": "Zinsinsi zolowera sizinaperekedwe.",
    "Invalid or expired verification code.": "Khodi yotsimikizira ndi yolakwika kapena yatha nthawi.",
    "Invalid username/email or password.": "Dzina, imelo kapena mawu achinsinsi ndi olakwika.",
    "Payments are not enabled.": "Malipiro sanayatse kugwira ntchito.",
    "Permission denied.": "Simunaloledwe kuchita izi.",
}


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    original = response.data
    detail = original.get("detail") if isinstance(original, dict) else None
    message = str(detail or "The request could not be completed.")
    language = str(getattr(request := context.get("request"), "headers", {}).get("Accept-Language", "en")).lower()
    if language.startswith("ny"): message = NY_MESSAGES.get(message, message)
    fields = {key: value for key, value in original.items() if key != "detail"} if isinstance(original, dict) else {}
    response.data = {
        "error": {"code": getattr(exc, "default_code", "api_error"), "message": message, "fields": fields},
        "detail": message,
        "correlation_id": getattr(request, "correlation_id", None),
    }
    return response
