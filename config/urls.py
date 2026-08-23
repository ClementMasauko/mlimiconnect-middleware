from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

def service_status(_request):
    return JsonResponse({"service": "MlimiConnect API", "status": "online", "frontend": settings.FRONTEND_URL})

urlpatterns = [
    path("", service_status, name="service-status"),
    path("health/", service_status, name="health"),
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
