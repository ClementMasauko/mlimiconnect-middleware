from dataclasses import dataclass
from django.conf import settings


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    configured: bool


def provider_statuses():
    return [
        ProviderStatus("payments", bool(settings.PAYMENTS_ENABLED and getattr(settings, "PAYMENT_PROVIDER", "") == "paychangu" and getattr(settings, "PAYCHANGU_SECRET_KEY", "") and getattr(settings, "PAYMENT_WEBHOOK_SECRET", ""))),
        ProviderStatus("weather", bool(getattr(settings, "WEATHER_PROVIDER", "") == "open_meteo" and getattr(settings, "WEATHER_API_URL", ""))),
        ProviderStatus("diagnosis", bool(getattr(settings, "DIAGNOSIS_PROVIDER", "") and getattr(settings, "DIAGNOSIS_API_KEY", ""))),
        ProviderStatus("official_market", bool(getattr(settings, "MARKET_DATA_PROVIDER", "") and getattr(settings, "MARKET_DATA_API_KEY", ""))),
        ProviderStatus("logistics", bool(getattr(settings, "LOGISTICS_PROVIDER", "") and getattr(settings, "LOGISTICS_API_KEY", "") and getattr(settings, "LOGISTICS_API_URL", ""))),
        ProviderStatus("error_reporting", bool(getattr(settings, "SENTRY_DSN", ""))),
        ProviderStatus("veterinary_data", bool(getattr(settings, "VETERINARY_DATA_PROVIDER", "") and getattr(settings, "VETERINARY_DATA_API_URL", "") and getattr(settings, "VETERINARY_DATA_API_KEY", ""))),
    ]


def require_provider(name):
    status = next((item for item in provider_statuses() if item.name == name), None)
    if not status or not status.configured:
        raise ProviderUnavailable(f"The {name} provider is not configured.")
    return status
