import json
import logging
import re
import time
import uuid
from urllib.request import Request, urlopen
from django.conf import settings

SENSITIVE_KEYS = re.compile(r"password|passcode|pin|secret|token|authorization|cookie|phone|email|financial|account|message", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?265|0)?\d{9}(?!\d)")

def redact(value, key=""):
    if SENSITIVE_KEYS.search(str(key)): return "[REDACTED]"
    if isinstance(value, dict): return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [redact(v) for v in value]
    if isinstance(value, str): return PHONE.sub("[REDACTED_PHONE]", value)[:2000]
    return value

class RedactionFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.msg)
        if record.args: record.args = tuple(redact(arg) for arg in record.args) if isinstance(record.args, tuple) else redact(record.args)
        return True

class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for key in ["correlation_id", "method", "path", "status_code", "duration_ms"]:
            if hasattr(record, key): payload[key] = getattr(record, key)
        if record.exc_info: payload["exception"] = self.formatException(record.exc_info)[:4000]
        return json.dumps(redact(payload), default=str, separators=(",", ":"))

def send_alert(title, detail, correlation_id):
    endpoint = getattr(settings, "ALERT_WEBHOOK_URL", "")
    if not endpoint: return
    body = json.dumps({"title": title, "detail": redact(detail), "correlation_id": correlation_id}).encode()
    try: urlopen(Request(endpoint, data=body, headers={"Content-Type": "application/json"}, method="POST"), timeout=3).close()
    except Exception: logging.getLogger("mlimiconnect.alerting").exception("Alert delivery failed", extra={"correlation_id": correlation_id})

class ObservabilityMiddleware:
    def __init__(self, get_response): self.get_response = get_response; self.logger = logging.getLogger("mlimiconnect.request")
    def __call__(self, request):
        correlation_id = request.headers.get("X-Correlation-ID", "")[:64] or str(uuid.uuid4()); request.correlation_id = correlation_id; started = time.perf_counter()
        try: response = self.get_response(request)
        except Exception:
            duration = int((time.perf_counter()-started)*1000); self.logger.exception("Unhandled request error", extra={"correlation_id": correlation_id, "method": request.method, "path": request.path, "status_code": 500, "duration_ms": duration}); send_alert("Backend request failure", {"path": request.path, "status": 500}, correlation_id); raise
        duration = int((time.perf_counter()-started)*1000); response["X-Correlation-ID"] = correlation_id; response["Server-Timing"] = f"app;dur={duration}"
        self.logger.info("request.completed", extra={"correlation_id": correlation_id, "method": request.method, "path": request.path, "status_code": response.status_code, "duration_ms": duration})
        if response.status_code >= 500: send_alert("Backend 5xx response", {"path": request.path, "status": response.status_code}, correlation_id)
        try:
            from .models import OperationalEvent
            if request.path.startswith("/api/") or response.status_code >= 400: OperationalEvent.objects.create(category="http", name=request.resolver_match.route[:120] if request.resolver_match else "api", status=str(response.status_code), duration_ms=duration, correlation_id=correlation_id, metadata={"method": request.method})
        except Exception: self.logger.exception("Operational metric write failed", extra={"correlation_id": correlation_id})
        return response
