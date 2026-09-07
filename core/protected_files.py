from pathlib import PurePath

from django.http import FileResponse, Http404, HttpResponseRedirect

from .models import AnimalWelfareReport, DeliveryEvidence, LiveAnimalListingDetail, LivestockHealthEvent, OrganizationDocument, TraceabilityEvidence, TransporterDocument


def protected_file_link(kind, object_id):
    return f"/api/protected-files/{kind}/{object_id}/"


def _resolve(user, kind, object_id):
    if kind == "organization-document":
        row = OrganizationDocument.objects.select_related("organization__owner").filter(id=object_id).first()
        allowed = row and (user.is_staff or row.organization.owner_id == user.id or row.organization.team_members.filter(user=user, status="active").exists())
        field = row.file if row else None
    elif kind == "transporter-document":
        row = TransporterDocument.objects.select_related("profile__user").filter(id=object_id).first()
        allowed, field = bool(row and (user.is_staff or row.profile.user_id == user.id)), row.file if row else None
    elif kind == "traceability-evidence":
        row = TraceabilityEvidence.objects.select_related("event__batch").filter(id=object_id).first()
        allowed, field = bool(row and (user.is_staff or row.event.batch.owner_id == user.id)), row.file if row else None
    elif kind == "delivery-evidence":
        row = DeliveryEvidence.objects.select_related("order").filter(id=object_id).first()
        allowed = bool(row and (user.is_staff or row.order.buyer_id == user.id or row.order.items.filter(listing__seller=user).exists() or (hasattr(row.order, "delivery") and row.order.delivery.transporter_id == user.id)))
        field = row.file if row else None
    elif kind == "livestock-health-evidence":
        row = LivestockHealthEvent.objects.select_related("herd").filter(id=object_id).first()
        allowed, field = bool(row and (user.is_staff or row.herd.owner_id == user.id)), row.evidence if row else None
    elif kind == "veterinary-certificate":
        row = LiveAnimalListingDetail.objects.select_related("listing").filter(id=object_id).first()
        allowed, field = bool(row and (user.is_staff or row.listing.seller_id == user.id)), row.veterinary_certificate if row else None
    elif kind == "welfare-evidence":
        row = AnimalWelfareReport.objects.filter(id=object_id).first()
        allowed, field = bool(row and (user.is_staff or row.reporter_id == user.id)), row.evidence if row else None
    else:
        raise Http404
    if not allowed or not field:
        raise Http404
    return field


def serve_protected_file(request, kind, object_id):
    field = _resolve(request.user, kind, object_id)
    storage = field.storage
    response = HttpResponseRedirect(field.url) if getattr(storage, "cloudinary_enabled", False) else FileResponse(field.open("rb"), filename=PurePath(field.name).name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
