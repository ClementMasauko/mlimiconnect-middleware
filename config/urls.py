from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from core.health import liveness, readiness

def service_status(_request):
    return JsonResponse({"service": "MlimiConnect API", "status": "online"})

urlpatterns = [
    path("", service_status, name="service-status"),
    path("live/", liveness, name="liveness"),
    path("health/", readiness, name="health"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    path("api/v1/", include(("core.urls", "core"), namespace="api-v1")),
    path("api/schema/", SpectacularAPIView.as_view(urlconf="config.schema_urls"), name="openapi-schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="openapi-schema"), name="api-docs"),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
