import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-this-secret-key-before-production")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [v.strip() for v in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if v.strip()]

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "corsheaders", "rest_framework", "drf_spectacular", "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "core.observability.ObservabilityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"

database_url = os.getenv("DATABASE_URL", "")
if database_url.startswith(("postgres://", "postgresql://")):
    parsed_db = urlparse(database_url)
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": parsed_db.path.lstrip("/"), "USER": unquote(parsed_db.username or ""), "PASSWORD": unquote(parsed_db.password or ""), "HOST": parsed_db.hostname or "", "PORT": parsed_db.port or 5432, "OPTIONS": {"sslmode": "require"}}}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.getenv("E2E_DATABASE_PATH", BASE_DIR / "db.sqlite3")}}
AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Blantyre"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_STORAGE_PROVIDER = os.getenv("MEDIA_STORAGE_PROVIDER", "filesystem")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "")
PROTECTED_MEDIA_URL_TTL_SECONDS = int(os.getenv("PROTECTED_MEDIA_URL_TTL_SECONDS", "300"))
if MEDIA_STORAGE_PROVIDER == "cloudinary" and CLOUDINARY_URL:
    import cloudinary
    cloudinary.config(cloudinary_url=CLOUDINARY_URL, secure=True)
STORAGES = {"default": {"BACKEND": "core.storage.AdaptiveCloudinaryStorage"}, "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

def origin_list(name, default):
    return [value.strip().rstrip("/") for value in os.getenv(name, default).split(",") if value.strip()]

CORS_ALLOWED_ORIGINS = origin_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = origin_list("CSRF_TRUSTED_ORIGINS", "http://localhost:5173")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "false").lower() == "true"
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0"))
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DJANGO_DATA_UPLOAD_MAX_MEMORY_SIZE", str(2 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DJANGO_FILE_UPLOAD_MAX_MEMORY_SIZE", str(5 * 1024 * 1024)))
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle", "rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": "100/hour", "user": "1000/hour", "ussd": "120/minute", "geocoding": "10/minute"},
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 24,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.errors.api_exception_handler",
}
SPECTACULAR_SETTINGS = {"TITLE": "MlimiConnect API", "DESCRIPTION": "Versioned marketplace, advisory, logistics and administration API.", "VERSION": "1.0.0", "SERVE_INCLUDE_SCHEMA": False}
PAYMENTS_ENABLED = os.getenv("PAYMENTS_ENABLED", "false").lower() == "true"
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "")
E2E_MODE = os.getenv("E2E_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "test")
PAYMENT_CURRENCY = os.getenv("PAYMENT_CURRENCY", "MWK").upper()
PAYCHANGU_API_URL = (os.getenv("PAYCHANGU_API_URL", "") or "https://api.paychangu.com").rstrip("/")
PAYCHANGU_PUBLIC_KEY = os.getenv("PAYCHANGU_PUBLIC_KEY", "")
PAYCHANGU_SECRET_KEY = os.getenv("PAYCHANGU_SECRET_KEY", "")
PAYCHANGU_TIMEOUT_SECONDS = float(os.getenv("PAYCHANGU_TIMEOUT_SECONDS", "10"))
WEATHER_PROVIDER = os.getenv("WEATHER_PROVIDER", "open_meteo") or "open_meteo"
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "")
WEATHER_API_URL = (os.getenv("WEATHER_API_URL", "") or "https://api.open-meteo.com/v1/forecast").rstrip("/")
WEATHER_TIMEOUT_SECONDS = float(os.getenv("WEATHER_TIMEOUT_SECONDS", "5"))
WEATHER_CACHE_SECONDS = int(os.getenv("WEATHER_CACHE_SECONDS", "1200"))
WEATHER_STALE_SECONDS = int(os.getenv("WEATHER_STALE_SECONDS", "21600"))
DIAGNOSIS_PROVIDER, DIAGNOSIS_API_KEY = os.getenv("DIAGNOSIS_PROVIDER", ""), os.getenv("DIAGNOSIS_API_KEY", "")
DIAGNOSIS_API_URL = os.getenv("DIAGNOSIS_API_URL", "https://crop.kindwise.com/api/v1").rstrip("/")
DIAGNOSIS_TIMEOUT_SECONDS = float(os.getenv("DIAGNOSIS_TIMEOUT_SECONDS", "20"))
DIAGNOSIS_MAX_IMAGE_BYTES = int(os.getenv("DIAGNOSIS_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MARKET_DATA_PROVIDER, MARKET_DATA_API_KEY = os.getenv("MARKET_DATA_PROVIDER", ""), os.getenv("MARKET_DATA_API_KEY", "")
LOGISTICS_PROVIDER, LOGISTICS_API_KEY = os.getenv("LOGISTICS_PROVIDER", ""), os.getenv("LOGISTICS_API_KEY", "")
VETERINARY_DATA_PROVIDER = os.getenv("VETERINARY_DATA_PROVIDER", "")
VETERINARY_DATA_API_URL = os.getenv("VETERINARY_DATA_API_URL", "")
VETERINARY_DATA_API_KEY = os.getenv("VETERINARY_DATA_API_KEY", "")
LOGISTICS_API_URL = os.getenv("LOGISTICS_API_URL", "")
GEOCODING_PROVIDER = os.getenv("GEOCODING_PROVIDER", "nominatim")
GEOCODING_API_URL = os.getenv("GEOCODING_API_URL", "https://nominatim.openstreetmap.org").rstrip("/")
GEOCODING_TIMEOUT_SECONDS = float(os.getenv("GEOCODING_TIMEOUT_SECONDS", "8"))
GEOCODING_CACHE_SECONDS = int(os.getenv("GEOCODING_CACHE_SECONDS", str(30 * 24 * 60 * 60)))
USSD_SERVICE_KEY = os.getenv("USSD_SERVICE_KEY", "")
USSD_ALLOWED_IPS = [value.strip() for value in os.getenv("USSD_ALLOWED_IPS", "127.0.0.1,::1").split(",") if value.strip()]
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+265999000000")
SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@mlimiconnect.mw")
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_FILE_PATH = os.getenv("EMAIL_FILE_PATH", str(BASE_DIR / "test-emails"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "support@mlimiconnect.mw")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "")
SMS_ENABLED = os.getenv("SMS_ENABLED", "false").lower() == "true"
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "")
TEXTBEE_API_URL = os.getenv("TEXTBEE_API_URL", "https://api.textbee.dev/api/v1").rstrip("/")
TEXTBEE_API_KEY = os.getenv("TEXTBEE_API_KEY", "")
TEXTBEE_DEVICE_ID = os.getenv("TEXTBEE_DEVICE_ID", "")
SMS_SENDER_NUMBER = os.getenv("SMS_SENDER_NUMBER", "")
if "test" in sys.argv:
    EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    SMS_ENABLED = False
    MEDIA_STORAGE_PROVIDER = "filesystem"
    CLOUDINARY_URL = ""
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
LOGGING = {"version": 1, "disable_existing_loggers": False, "filters": {"redact": {"()": "core.observability.RedactionFilter"}}, "formatters": {"json": {"()": "core.observability.JsonFormatter"}}, "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "filters": ["redact"]}}, "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")}, "loggers": {"django.server": {"handlers": ["console"], "level": "INFO", "propagate": False}, "mlimiconnect": {"handlers": ["console"], "level": "INFO", "propagate": False}}}

SENTRY_DSN = os.getenv("SENTRY_DSN", "")
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "production" if not DEBUG else "development")
SENTRY_RELEASE = os.getenv("SENTRY_RELEASE", os.getenv("RENDER_GIT_COMMIT", "development")[:40])
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0"))
SENTRY_ERROR_SAMPLE_RATE = float(os.getenv("SENTRY_ERROR_SAMPLE_RATE", "1"))
from core.sentry import configure_sentry
configure_sentry(SENTRY_DSN, SENTRY_ENVIRONMENT, SENTRY_RELEASE, SENTRY_TRACES_SAMPLE_RATE, SENTRY_ERROR_SAMPLE_RATE)

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_TIMEOUT = float(os.getenv("EMAIL_TIMEOUT_SECONDS", "8"))
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "smtp").strip().lower()
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
BREVO_API_URL = os.getenv("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email").strip()
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL",
    "MlimiConnect <clementlyson99@gmail.com>",
)
