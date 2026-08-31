import re

EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?265|0)?(?:8[0-9]|9[0-9])(?:[\s-]*\d){7}(?!\w)")
SECRET = re.compile(r"(?i)(bearer\s+|api[_-]?key[=: ]+|token[=: ]+|password[=: ]+)[^\s,;]+")
SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "password", "pin", "token", "api_key", "secret", "phone", "email", "message", "body", "data"}

def redact_text(value):
    text = str(value)
    text = EMAIL.sub("[REDACTED_EMAIL]", text)
    text = PHONE.sub("[REDACTED_PHONE]", text)
    return SECRET.sub("[REDACTED_SECRET]", text)[:4000]

def scrub(value, key=""):
    if key.lower().replace("-", "_") in SENSITIVE_KEYS: return "[REDACTED]"
    if isinstance(value, dict): return {str(k)[:100]: scrub(v, str(k)) for k, v in value.items()}
    if isinstance(value, list): return [scrub(item) for item in value[:50]]
    if isinstance(value, tuple): return tuple(scrub(item) for item in value[:50])
    if isinstance(value, str): return redact_text(value)
    return value

def before_send(event, hint):
    event.pop("request", None)
    event.pop("user", None)
    event.pop("breadcrumbs", None)
    event.pop("modules", None)
    if event.get("message"): event["message"] = redact_text(event["message"])
    exception = event.get("exception") or {}
    for value in exception.get("values") or []:
        if value.get("value"): value["value"] = redact_text(value["value"])
    event["extra"] = scrub(event.get("extra") or {})
    event["contexts"] = scrub({k: v for k, v in (event.get("contexts") or {}).items() if k not in {"device", "runtime"}})
    return event

def configure_sentry(dsn, environment, release, traces_sample_rate=0, error_sample_rate=1):
    if not dsn: return False
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    sentry_sdk.init(
        dsn=dsn, environment=environment, release=release,
        integrations=[DjangoIntegration()], send_default_pii=False,
        before_send=before_send, max_breadcrumbs=0, attach_stacktrace=True,
        traces_sample_rate=max(0.0, min(float(traces_sample_rate), 1.0)),
        sample_rate=max(0.0, min(float(error_sample_rate), 1.0)),
    )
    return True
