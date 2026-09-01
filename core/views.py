from django.conf import settings
from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.db import transaction
from django.db.models import Case, Count, DecimalField, ExpressionWrapper, F, Q, Sum, Value, When
from django.db.models.functions import TruncMonth
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import password_validation
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import csv
from django.http import HttpResponse
import secrets
import math
import hashlib
import hmac
import json
from rest_framework import generics, permissions, serializers as drf_serializers, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import SimpleRateThrottle
from .models import AccountDeletionRequest, AdvisoryUsage, AnimalRecord, AnimalWeightRecord, AnimalWelfareReport, AuditLog, ChatMessage, Conversation, CropDiagnosis, Delivery, DeliveryEvidence, DeliveryLocationUpdate, DeliveryQuote, DeliveryRating, DiagnosisEscalation, DiagnosisReport, Dispute, EmailVerificationRequest, ExpertConsultation, FavouriteListing, HerdFlock, HistoricalMarketPrice, Listing, LiveAnimalListingDetail, LivestockAlert, LivestockBreedingRecord, LivestockCatalogueEntry, LivestockDeliveryRequirement, LivestockFinancialRecord, LivestockHealthEvent, LivestockMovementRestriction, LivestockProductionRecord, LivestockProfile, NewsletterSubscription, Notification, NotificationPreference, OperationalEvent, Order, OrderReview, Organization, OrganizationDocument, OrganizationMember, PasswordResetRequest, PaymentReconciliation, PlatformSetting, RecentlyViewedListing, Refund, SavedSearch, ServiceIncident, SmartContract, Subscription, TeamApprovalRequest, TraceabilityAudit, TraceabilityBatch, TraceabilityEvent, TransporterDocument, TransporterProfile, USSDCredential, User, VaccinationReminder, WalletTransaction, WantedListing
from .order_lifecycle import transition_order
from .communications import deliver_security_code
from .providers import provider_statuses
from .providers import ProviderUnavailable
from .logistics import create_external_shipment
from .traceability import append_event
from .admin_audit import audit_change, snapshot
from .weather import WeatherUnavailable, get_weather
from .diagnosis import CONSENT_VERSION, DISCLAIMER, SUPPORTED_CROPS, DiagnosisError, delete_remote, identify, prepare_image
from .geocoding import ATTRIBUTION, ATTRIBUTION_URL, GeocodingError, GeocodingRateLimited, read_selection, search_malawi, sign_selection
from .payments import PaymentProviderError, extract_transaction_reference, initialize_checkout, verify_and_reconcile
from .crop_planning import build_crop_plan
from .serializers import CheckoutSerializer, ContactSerializer, ConversationSerializer, ListingSerializer, LoginSerializer, MessageSerializer, NewsletterSerializer, NotificationSerializer, OrderReviewSerializer, OrderSerializer, OrganizationSerializer, RegisterSerializer, SubscriptionSerializer, TraceabilityBatchSerializer, UserSerializer

# APIView does not provide serializer metadata. This empty default keeps every
# custom endpoint present in the generated OpenAPI document; concrete generic
# views and annotated serializers continue to provide their richer schemas.
class EmptySchemaSerializer(drf_serializers.Serializer):
    pass
APIView.serializer_class = EmptySchemaSerializer

class GeocodingThrottle(SimpleRateThrottle):
    scope = "geocoding"
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": request.user.pk}
APIView.get_serializer = lambda self, *args, **kwargs: self.serializer_class(*args, **kwargs)

class USSDRateThrottle(SimpleRateThrottle):
    scope="ussd"
    def get_cache_key(self,request,view):return self.cache_format%{"scope":self.scope,"ident":self.get_ident(request)}

def ussd_source_allowed(request):
    forwarded=request.META.get("HTTP_X_FORWARDED_FOR","").split(",")[0].strip();ip=forwarded or request.META.get("REMOTE_ADDR","");return not settings.USSD_ALLOWED_IPS or ip in settings.USSD_ALLOWED_IPS

class StandardPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100

def organization_access(user):
    if hasattr(user, "organization"): return user.organization, None
    membership = OrganizationMember.objects.filter(user=user, status="active").select_related("organization").first()
    return (membership.organization, membership) if membership else (None, None)

def require_organization_permission(user, permission=None):
    organization, membership = organization_access(user)
    if not organization: raise PermissionDenied("An active organisation membership is required.")
    if membership and permission and not getattr(membership, permission, False): raise PermissionDenied("Your delegated organisation role does not include this permission.")
    return organization, membership

class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def get(self, request): return Response({"csrfToken": get_token(request)})

@method_decorator(csrf_protect, name="dispatch")
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        user = User.objects.filter(email__iexact=str(request.data.get("email", "")).strip()).first()
        verification = EmailVerificationRequest.objects.filter(user=user, used=False).order_by("-created_at").first() if user else None
        if not verification or not verification.verify_code(str(request.data.get("otp", ""))):
            return Response({"detail": "Invalid or expired verification code."}, status=400)
        verification.used = True; verification.save(update_fields=["used"])
        user.email_verified = True; user.is_active = True; user.save(update_fields=["email_verified", "is_active"])
        return Response({"verified": True})

@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return Response({"user": UserSerializer(user).data})

class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)

class ProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    def get_object(self): return self.request.user

class PublicListingList(generics.ListAPIView):
    serializer_class = ListingSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    queryset = Listing.objects.filter(is_active=True, approval_status="approved").select_related("seller", "organization").prefetch_related("wholesale_tiers")
    pagination_class = StandardPagination
    def get_queryset(self):
        today, now, params = timezone.localdate(), timezone.now(), self.request.query_params
        queryset = super().get_queryset().filter(Q(listing_expires_at__isnull=True) | Q(listing_expires_at__gt=now), Q(expiry_date__isnull=True) | Q(expiry_date__gte=today)).order_by("-created_at")
        query = str(params.get("q", "")).strip()
        if query: queryset = queryset.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(category__icontains=query) | Q(variety__icontains=query) | Q(grade__icontains=query) | Q(certification__icontains=query) | Q(seller__username__icontains=query))
        if params.get("category"): queryset = queryset.filter(category=params["category"])
        if params.get("location"): queryset = queryset.filter(seller__location__iexact=params["location"])
        if params.get("listing_type"): queryset = queryset.filter(listing_type=params["listing_type"])
        if params.get("condition") == "new": queryset = queryset.filter(condition="new")
        if params.get("condition") == "used": queryset = queryset.filter(condition__startswith="used")
        if params.get("unit"): queryset = queryset.filter(unit=params["unit"])
        if params.get("available_on"): queryset = queryset.filter(Q(available_from__isnull=True) | Q(available_from__lte=params["available_on"]), Q(expiry_date__isnull=True) | Q(expiry_date__gte=params["available_on"]))
        if params.get("verified_only") == "true": queryset = queryset.filter(Q(organization__verification_status="verified") | Q(seller__is_buyer_verified=True))
        if params.get("organic") == "true": queryset = queryset.filter(is_organic=True)
        if params.get("wholesale") == "true": queryset = queryset.filter(wholesale_tiers__isnull=False).distinct()
        if params.get("minimum_wholesale_quantity"): queryset = queryset.filter(wholesale_tiers__minimum_quantity__lte=params["minimum_wholesale_quantity"]).distinct()
        if params.get("allow_partial") == "true": queryset = queryset.filter(allow_partial_fulfilment=True)
        multiplier = Case(When(unit="tonne", then=Value(1000)), default=Value(1), output_field=DecimalField())
        queryset = queryset.annotate(unit_normalized_price=ExpressionWrapper(F("price") / (F("pack_size") * multiplier), output_field=DecimalField(max_digits=16, decimal_places=4)))
        try:
            if params.get("normalized_price_min"): queryset = queryset.filter(unit_normalized_price__gte=params["normalized_price_min"])
            if params.get("normalized_price_max"): queryset = queryset.filter(unit_normalized_price__lte=params["normalized_price_max"])
        except (TypeError, ValueError): pass
        ordering = {"price_low": "unit_normalized_price", "price_high": "-unit_normalized_price", "newest": "-created_at"}.get(params.get("sort"))
        if ordering: queryset = queryset.order_by(ordering)
        try:
            lat, lon, radius = float(params.get("latitude")), float(params.get("longitude")), float(params.get("radius_km"))
            matching = []
            for row in queryset.exclude(latitude=None).exclude(longitude=None).values("id", "latitude", "longitude", "delivery_radius_km"):
                dlat, dlon = math.radians(float(row["latitude"])-lat), math.radians(float(row["longitude"])-lon); a = math.sin(dlat/2)**2 + math.cos(math.radians(lat))*math.cos(math.radians(float(row["latitude"])))*math.sin(dlon/2)**2; distance = 6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
                if distance <= radius and (row["delivery_radius_km"] is None or distance <= row["delivery_radius_km"]): matching.append(row["id"])
            queryset = queryset.filter(id__in=matching)
        except (TypeError, ValueError): pass
        return queryset

class ListingListCreate(generics.ListCreateAPIView):
    serializer_class = ListingSerializer
    def get_queryset(self):
        organization, membership = organization_access(self.request.user)
        query = Q(seller=self.request.user)
        if organization and (membership is None or membership.can_manage_listings): query |= Q(organization=organization, shared_with_team=True)
        return Listing.objects.filter(query).select_related("seller", "organization").distinct()
    def perform_create(self, serializer):
        organization, membership = organization_access(self.request.user)
        if membership and not membership.can_manage_listings: raise PermissionDenied("Your organisation role cannot create shared listings.")
        if not self.request.user.can_sell and not organization: raise PermissionDenied("This account is not enabled to sell.")
        if organization and organization.verification_status != "verified": raise PermissionDenied("Organization verification is required before publishing listings.")
        serializer.save(seller=self.request.user, organization=organization, shared_with_team=bool(organization))

class ListingDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ListingSerializer
    def get_queryset(self):
        organization, membership = organization_access(self.request.user)
        query = Q(seller=self.request.user)
        if organization and (membership is None or membership.can_manage_listings): query |= Q(organization=organization, shared_with_team=True)
        return Listing.objects.filter(query).distinct()
    def perform_destroy(self, instance): instance.is_active = False; instance.save(update_fields=["is_active"])

class SavedSearchesView(APIView):
    def get(self, request): return Response([{"id": row.id, "name": row.name, "filters": row.filters, "created_at": row.created_at} for row in request.user.saved_searches.order_by("-created_at")])
    def post(self, request):
        name, filters = str(request.data.get("name", "")).strip(), request.data.get("filters", {})
        if not name or not isinstance(filters, dict): return Response({"detail": "Search name and filter object are required."}, status=400)
        row = SavedSearch.objects.create(user=request.user, name=name[:100], filters=filters)
        return Response({"id": row.id, "name": row.name, "filters": row.filters}, status=201)
    def delete(self, request):
        row = generics.get_object_or_404(SavedSearch, id=request.data.get("id"), user=request.user); row.delete(); return Response(status=204)

class WantedListingsView(APIView):
    def get(self, request):
        rows = WantedListing.objects.filter(Q(status="open") | Q(buyer=request.user)).select_related("buyer").order_by("-created_at")
        return Response([{"id": row.id, "buyer": row.buyer.username, "title": row.title, "description": row.description, "category": row.category, "quantity": row.quantity, "unit": row.unit, "maximum_price": row.maximum_price, "needed_by": row.needed_by, "location": row.location, "status": row.status, "created_at": row.created_at} for row in rows])
    def post(self, request):
        try: quantity = int(request.data.get("quantity"))
        except (TypeError, ValueError): return Response({"detail": "Quantity must be a positive whole number."}, status=400)
        unit = request.data.get("unit", "item")
        if quantity < 1 or unit not in dict(Listing.UNITS): return Response({"detail": "Valid quantity and unit are required."}, status=400)
        row = WantedListing.objects.create(buyer=request.user, title=str(request.data.get("title", ""))[:180], description=str(request.data.get("description", "")), category=str(request.data.get("category", ""))[:80], quantity=quantity, unit=unit, maximum_price=request.data.get("maximum_price") or None, needed_by=request.data.get("needed_by") or None, location=str(request.data.get("location", ""))[:180])
        if not row.title or not row.description or not row.category: row.delete(); return Response({"detail": "Title, description and category are required."}, status=400)
        return Response({"id": row.id, "status": row.status}, status=201)
    def patch(self, request):
        row = generics.get_object_or_404(WantedListing, id=request.data.get("id"), buyer=request.user)
        if request.data.get("status") not in dict(WantedListing.STATUSES): return Response({"detail": "Invalid wanted-listing status."}, status=400)
        row.status = request.data["status"]; row.save(update_fields=["status"]); return Response({"id": row.id, "status": row.status})

class FavouritesView(APIView):
    def get(self, request):
        rows = Listing.objects.filter(favourited_by__user=request.user, is_active=True).select_related("seller", "organization").prefetch_related("wholesale_tiers")
        return Response(ListingSerializer(rows, many=True).data)
    def post(self, request):
        listing = generics.get_object_or_404(Listing, id=request.data.get("listing_id"), is_active=True)
        _, created = FavouriteListing.objects.get_or_create(user=request.user, listing=listing)
        return Response({"listing_id": listing.id, "favourited": True}, status=201 if created else 200)
    def delete(self, request):
        FavouriteListing.objects.filter(user=request.user, listing_id=request.data.get("listing_id")).delete(); return Response(status=204)

class RecentlyViewedView(APIView):
    def get(self, request):
        rows = Listing.objects.filter(viewed_by__user=request.user, is_active=True).select_related("seller", "organization").prefetch_related("wholesale_tiers").order_by("-viewed_by__viewed_at")[:30]
        return Response(ListingSerializer(rows, many=True).data)
    def post(self, request):
        listing = generics.get_object_or_404(Listing, id=request.data.get("listing_id"), is_active=True)
        row, _ = RecentlyViewedListing.objects.update_or_create(user=request.user, listing=listing, defaults={}); row.save()
        keep_ids = list(request.user.recently_viewed_listings.order_by("-viewed_at").values_list("id", flat=True)[:30]); request.user.recently_viewed_listings.exclude(id__in=keep_ids).delete()
        return Response({"listing_id": listing.id, "viewed_at": row.viewed_at}, status=201)

class ListingComparisonView(APIView):
    def get(self, request):
        try: ids = [int(value) for value in request.query_params.get("ids", "").split(",") if value][:4]
        except ValueError: return Response({"detail": "Comparison IDs must be numbers."}, status=400)
        if len(ids) < 2: return Response({"detail": "Choose between two and four listings."}, status=400)
        rows = Listing.objects.filter(id__in=ids, is_active=True, approval_status="approved").select_related("seller", "organization").prefetch_related("wholesale_tiers")
        return Response(ListingSerializer(rows, many=True).data)

class ContactCreate(generics.CreateAPIView):
    serializer_class = ContactSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

class NewsletterCreate(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        serializer = NewsletterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subscription, _ = NewsletterSubscription.objects.update_or_create(email=serializer.validated_data["email"].lower(), defaults={"active": True})
        return Response(NewsletterSerializer(subscription).data, status=status.HTTP_201_CREATED)

class CheckoutView(APIView):
    def post(self, request):
        organization, membership = organization_access(request.user)
        if membership and not membership.can_procure: return Response({"detail": "Your delegated role does not include procurement permission."}, status=403)
        if not request.user.can_buy and not organization: return Response({"detail": "This account is not enabled to buy."}, status=403)
        if not settings.PAYMENTS_ENABLED:
            return Response({"detail": "Payments are not enabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if settings.PAYMENT_PROVIDER != "paychangu" and not (settings.E2E_MODE and settings.PAYMENT_PROVIDER == "e2e"):
            return Response({"detail": "The configured payment provider is not supported."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            # Roll back the order and stock reservation if PayChangu cannot
            # create the hosted checkout session.
            with transaction.atomic():
                order = serializer.save()
                if settings.E2E_MODE and settings.PAYMENT_PROVIDER == "e2e":
                    tx_ref = f"e2e-{order.id}"
                    checkout_url = f"{settings.FRONTEND_URL}/app/checkout?e2e_payment={tx_ref}"
                    order.provider_reference = tx_ref
                    order.save(update_fields=["provider_reference"])
                else:
                    checkout_url, tx_ref = initialize_checkout(order)
        except PaymentProviderError as error:
            return Response({"detail": str(error)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"order": OrderSerializer(order).data, "checkout_url": checkout_url, "tx_ref": tx_ref}, status=status.HTTP_201_CREATED)

class OrderList(generics.ListAPIView):
    serializer_class = OrderSerializer
    def get_queryset(self):
        organization, membership = organization_access(self.request.user)
        query = Q(buyer=self.request.user)
        if organization and (membership is None or membership.can_procure): query |= Q(organization=organization)
        return Order.objects.filter(query).distinct()

class SellerOrderList(generics.ListAPIView):
    serializer_class = OrderSerializer
    def get_queryset(self): return Order.objects.filter(items__listing__seller=self.request.user).distinct().order_by("-created_at")

class OrderDetail(generics.RetrieveAPIView):
    serializer_class = OrderSerializer
    def get_queryset(self): return Order.objects.filter(Q(buyer=self.request.user) | Q(items__listing__seller=self.request.user)).distinct()

class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view): return bool(request.user and request.user.is_authenticated and (request.user.user_type == "admin" or request.user.is_staff))

@method_decorator(csrf_exempt, name="dispatch")
class PaymentWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request, provider):
        correlation_id = getattr(request, "correlation_id", str(__import__("uuid").uuid4()))
        # PayChangu signs the unmodified request body in the `Signature` header.
        # Keep the generic header for existing/test providers.
        signature = request.headers.get("Signature", "") if provider.casefold() == "paychangu" else request.headers.get("X-Webhook-Signature", "")
        expected = hmac.new(settings.PAYMENT_WEBHOOK_SECRET.encode(), request.body, hashlib.sha256).hexdigest() if settings.PAYMENT_WEBHOOK_SECRET else ""
        valid = bool(expected and hmac.compare_digest(signature, expected)); status_name = "accepted" if valid else "rejected"
        OperationalEvent.objects.create(category="payment_webhook", name=provider[:120], status=status_name, correlation_id=correlation_id, metadata={"content_length": len(request.body)})
        if not valid: return Response({"detail": "Invalid webhook signature.", "correlation_id": correlation_id}, status=401)
        if provider.casefold() != "paychangu":
            return Response({"received": True, "correlation_id": correlation_id})
        tx_ref = extract_transaction_reference(request.data)
        if not tx_ref:
            return Response({"received": True, "processed": False, "reason": "No transaction reference.", "correlation_id": correlation_id})
        try:
            order, outcome = verify_and_reconcile(tx_ref)
        except PaymentProviderError:
            # A non-2xx response asks PayChangu to retry a transient failure.
            return Response({"detail": "Payment verification is temporarily unavailable.", "correlation_id": correlation_id}, status=503)
        OperationalEvent.objects.create(category="payment_webhook", name="paychangu_reconciliation", status=outcome, correlation_id=correlation_id, metadata={"order_id": getattr(order, "id", None)})
        return Response({"received": True, "processed": outcome in {"matched", "already_processed"}, "outcome": outcome, "correlation_id": correlation_id})

class PublicStatusView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def get(self, request):
        incidents = ServiceIncident.objects.filter(public=True).exclude(status="resolved").order_by("-started_at")
        services = [{"name": item.name, "status": "operational" if item.configured else "not_configured"} for item in provider_statuses()]
        services.insert(0, {"name": "api", "status": "operational"})
        return Response({"overall": "degraded" if incidents.exists() else "operational", "updated_at": timezone.now(), "services": services, "incidents": [{"id": row.id, "title": row.title, "service": row.service, "status": row.status, "message": row.message, "started_at": row.started_at} for row in incidents]})

class AdminOperationsMetrics(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        since = timezone.now() - timedelta(hours=24); rows = OperationalEvent.objects.filter(created_at__gte=since)
        return Response({"window_hours": 24, "requests": rows.filter(category="http").count(), "errors": rows.filter(category="http", status__gte="400").count(), "average_latency_ms": rows.filter(duration_ms__isnull=False).aggregate(value=__import__("django.db.models", fromlist=["Avg"]).Avg("duration_ms"))["value"] or 0, "payment_webhooks": {"accepted": rows.filter(category="payment_webhook", status="accepted").count(), "rejected": rows.filter(category="payment_webhook", status="rejected").count()}, "ussd": rows.filter(category="ussd").count(), "recent": list(rows.order_by("-created_at").values("category", "name", "status", "duration_ms", "correlation_id", "created_at")[:100])})

class AdminIncidentsView(APIView):
    permission_classes = [IsAdmin]
    def get(self, request): return Response(list(ServiceIncident.objects.order_by("-started_at").values("id", "title", "service", "status", "message", "started_at", "resolved_at", "public")))
    def post(self, request):
        status_value = request.data.get("status", "investigating")
        if status_value not in dict(ServiceIncident.STATUSES): return Response({"detail": "Invalid incident status."}, status=400)
        row = ServiceIncident.objects.create(title=str(request.data.get("title", ""))[:180], service=str(request.data.get("service", "platform"))[:80], status=status_value, message=str(request.data.get("message", "")), public=bool(request.data.get("public", True)))
        if not row.title or not row.message: row.delete(); return Response({"detail": "Incident title and message are required."}, status=400)
        return Response({"id": row.id, "status": row.status}, status=201)
    def patch(self, request):
        row = generics.get_object_or_404(ServiceIncident, id=request.data.get("id")); status_value = request.data.get("status")
        if status_value not in dict(ServiceIncident.STATUSES): return Response({"detail": "Invalid status."}, status=400)
        row.status, row.message = status_value, str(request.data.get("message", row.message)); row.resolved_at = timezone.now() if status_value == "resolved" else None; row.save(update_fields=["status", "message", "resolved_at"])
        return Response({"id": row.id, "status": row.status})

class OrderStatus(APIView):
    def patch(self, request, order_id):
        order = generics.get_object_or_404(Order, id=order_id)
        order = transition_order(order.id, request.user, request.data.get("status"), request.data.get("reason", ""), request.data.get("metadata", {}))
        return Response(OrderSerializer(order).data)

class OrderPartialFulfilment(APIView):
    @transaction.atomic
    def post(self, request, order_id):
        order = generics.get_object_or_404(Order.objects.select_for_update().filter(items__listing__seller=request.user).distinct(), id=order_id)
        quantities = request.data.get("items", [])
        if not isinstance(quantities, list) or not quantities: return Response({"detail": "Provide fulfilled item quantities."}, status=400)
        owned = {item.listing_id: item for item in order.items.select_for_update().filter(listing__seller=request.user)}
        for row in quantities:
            item = owned.get(row.get("listing_id"))
            try: quantity = int(row.get("fulfilled_quantity"))
            except (TypeError, ValueError): return Response({"detail": "Fulfilled quantities must be whole numbers."}, status=400)
            if not item or not item.listing.allow_partial_fulfilment or quantity < 0 or quantity > item.quantity: return Response({"detail": "Invalid quantity or listing does not permit partial fulfilment."}, status=400)
            item.fulfilled_quantity = quantity; item.save(update_fields=["fulfilled_quantity"])
        order = transition_order(order.id, request.user, "partially_fulfilled", request.data.get("reason", "Partial fulfilment recorded."), {"items": quantities})
        return Response(OrderSerializer(order).data)

class OrderDeliveryEvidence(APIView):
    def post(self, request, order_id):
        order = generics.get_object_or_404(Order, id=order_id)
        role_allowed = order.items.filter(listing__seller=request.user).exists() or (hasattr(order, "delivery") and order.delivery.transporter_id == request.user.id)
        if not role_allowed: raise PermissionDenied("Only the seller or assigned transporter may add delivery evidence.")
        evidence_type = request.data.get("evidence_type")
        if evidence_type not in dict(DeliveryEvidence.TYPES): return Response({"detail": "Invalid evidence type."}, status=400)
        upload = request.FILES.get("file")
        if upload and (upload.size > 5 * 1024 * 1024 or upload.content_type not in ["image/jpeg", "image/png", "application/pdf"]): return Response({"detail": "Evidence must be a JPG, PNG or PDF no larger than 5 MB."}, status=400)
        reference, note = str(request.data.get("reference", "")).strip(), str(request.data.get("note", "")).strip()
        signature = str(request.data.get("signature_name", "")).strip()
        if evidence_type in ["pickup", "delivery"] and not upload: return Response({"detail": "Pickup and delivery require photographic evidence."}, status=400)
        if evidence_type == "delivery" and not signature: return Response({"detail": "Recipient signature name is required for proof of delivery."}, status=400)
        if not upload and not reference: return Response({"detail": "Upload evidence or provide a reference."}, status=400)
        try: latitude, longitude = (Decimal(str(request.data[key])) if request.data.get(key) not in [None, ""] else None for key in ["latitude", "longitude"])
        except InvalidOperation: return Response({"detail": "Invalid evidence coordinates."}, status=400)
        evidence = DeliveryEvidence.objects.create(order=order, created_by=request.user, evidence_type=evidence_type, file=upload, reference=reference, note=note, location=str(request.data.get("location", ""))[:180], latitude=latitude, longitude=longitude, signature_name=signature[:140])
        return Response({"id": evidence.id, "evidence_type": evidence.evidence_type}, status=201)

class AdminOrderRefund(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def post(self, request, order_id):
        order = generics.get_object_or_404(Order.objects.select_for_update(), id=order_id)
        try: amount = __import__("decimal").Decimal(str(request.data.get("amount", order.total)))
        except __import__("decimal").InvalidOperation: return Response({"detail": "Enter a valid refund amount."}, status=400)
        provider, reference, reason = str(request.data.get("provider", "")).strip(), str(request.data.get("provider_reference", "")).strip(), str(request.data.get("reason", "")).strip()
        if amount <= 0 or amount > order.total or not provider or not reference or len(reason) < 5: return Response({"detail": "Amount, provider, unique reference and reason are required."}, status=400)
        provider_status = request.data.get("provider_status", "requested")
        if provider_status not in dict(Refund.STATUSES): return Response({"detail": "Invalid provider refund status."}, status=400)
        refund = Refund.objects.create(order=order, amount=amount, provider=provider, provider_reference=reference, reason=reason, requested_by=request.user, status=provider_status, provider_payload=request.data.get("provider_payload", {}))
        audit_change(actor=request.user, action="refund.created", target=refund, before={}, after=snapshot(refund, ["order", "amount", "provider", "provider_reference", "status"]), reason=reason)
        if refund.status == "settled": refund.settled_at = timezone.now(); refund.save(update_fields=["settled_at"]); transition_order(order.id, request.user, "refunded", reason, {"refund_id": refund.id, "provider_reference": reference})
        return Response({"id": refund.id, "status": refund.status, "provider_reference": refund.provider_reference}, status=201)

class AdminRefundStatus(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def patch(self, request, refund_id):
        refund = generics.get_object_or_404(Refund.objects.select_for_update().select_related("order"), id=refund_id)
        next_status = request.data.get("status")
        allowed = {"requested": ["submitted", "failed"], "submitted": ["settled", "failed"], "failed": ["submitted"]}
        if next_status not in allowed.get(refund.status, []): return Response({"detail": "Invalid refund-provider status transition."}, status=400)
        before = snapshot(refund, ["status", "provider_payload", "settled_at"])
        refund.status = next_status; refund.provider_payload = request.data.get("provider_payload", refund.provider_payload)
        fields = ["status", "provider_payload", "updated_at"]
        if next_status == "settled": refund.settled_at = timezone.now(); fields.append("settled_at")
        refund.save(update_fields=fields)
        audit_change(actor=request.user, action=f"refund.{next_status}", target=refund, before=before, after=snapshot(refund, ["status", "provider_payload", "settled_at"]), reason=refund.reason, extra={"provider_reference": refund.provider_reference})
        if next_status == "settled" and refund.order.status != "refunded": transition_order(refund.order_id, request.user, "refunded", refund.reason, {"refund_id": refund.id, "provider_reference": refund.provider_reference})
        return Response({"id": refund.id, "status": refund.status, "settled_at": refund.settled_at})

class OrderReviewCreate(generics.CreateAPIView):
    serializer_class = OrderReviewSerializer
    def perform_create(self, serializer):
        order = generics.get_object_or_404(Order, id=self.request.data.get("order"), buyer=self.request.user, status="fulfilled")
        serializer.save(order=order, reviewer=self.request.user)

class DashboardOverview(APIView):
    def get(self, request):
        buying = Order.objects.filter(buyer=request.user); selling = Order.objects.filter(items__listing__seller=request.user).distinct()
        return Response({"stats": {"activeListings": request.user.listings.filter(is_active=True).count(), "ordersPlaced": buying.count(), "sales": selling.count(), "revenue": selling.filter(status__in=["paid", "fulfilled"]).aggregate(value=Sum("total"))["value"] or 0, "unreadMessages": ChatMessage.objects.filter(conversation__participants=request.user).exclude(sender=request.user).exclude(read_by=request.user).count()}, "recentActivity": list(buying.order_by("-created_at").values("id", "status", "total", "created_at")[:8])})

class AnalyticsOverview(APIView):
    def get(self, request):
        orders = Order.objects.filter(Q(buyer=request.user) | Q(items__listing__seller=request.user)).distinct()
        by_status = list(orders.values("status").annotate(value=Count("id")).order_by("status"))
        by_category = list(Listing.objects.filter(seller=request.user).values("category").annotate(value=Count("id")).order_by("category"))
        return Response({"summary": {"orders": orders.count(), "volume": orders.aggregate(value=Sum("total"))["value"] or 0, "listings": request.user.listings.count()}, "ordersByStatus": by_status, "listingsByCategory": by_category})

class WalletView(APIView):
    def get(self, request):
        transactions = request.user.wallet_transactions.order_by("-created_at")
        credits = transactions.filter(amount__gt=0, status="completed").aggregate(value=Sum("amount"))["value"] or 0
        debits = transactions.filter(amount__lt=0, status="completed").aggregate(value=Sum("amount"))["value"] or 0
        return Response({"available": credits + debits, "pending": transactions.filter(status="pending").aggregate(value=Sum("amount"))["value"] or 0, "transactions": list(transactions.values("id", "type", "amount", "status", "reference", "created_at")[:50])})

class WithdrawalCreate(APIView):
    def post(self, request):
        try: amount = __import__("decimal").Decimal(str(request.data.get("amount")))
        except Exception: return Response({"detail": "Enter a valid amount."}, status=400)
        if amount <= 0: return Response({"detail": "Amount must be positive."}, status=400)
        transaction = WalletTransaction.objects.create(user=request.user, type="withdrawal", amount=-amount, status="pending", reference=f"WD-{request.user.id}-{int(timezone.now().timestamp())}", metadata={"provider": request.data.get("provider"), "phone": request.data.get("phone")})
        return Response({"id": transaction.id, "status": transaction.status, "reference": transaction.reference}, status=201)

class MarketData(APIView):
    def get(self, request):
        rows = Listing.objects.filter(is_active=True).values("category").annotate(average_price=__import__("django.db.models", fromlist=["Avg"]).Avg("price"), listings=Count("id")).order_by("category")
        return Response({"updated_at": timezone.now(), "markets": list(rows)})

class HistoricalMarketData(APIView):
    """Query World Bank monthly estimates without presenting them as live quotes."""
    def get(self, request):
        queryset = HistoricalMarketPrice.objects.all()
        crop = str(request.query_params.get("crop", "")).strip().lower()
        district = str(request.query_params.get("district", "")).strip()
        market = str(request.query_params.get("market", "")).strip()
        date_from = str(request.query_params.get("date_from", "")).strip()
        date_to = str(request.query_params.get("date_to", "")).strip()
        if crop:
            if crop not in dict(HistoricalMarketPrice.CROP_CHOICES):
                return Response({"detail": "Crop must be beans, cassava, groundnuts, maize or rice."}, status=400)
            queryset = queryset.filter(crop=crop)
        if district: queryset = queryset.filter(district__iexact=district)
        if market: queryset = queryset.filter(market__icontains=market)
        try:
            if date_from: queryset = queryset.filter(price_date__gte=__import__("datetime").date.fromisoformat(date_from))
            if date_to: queryset = queryset.filter(price_date__lte=__import__("datetime").date.fromisoformat(date_to))
        except ValueError:
            return Response({"detail": "Dates must use YYYY-MM-DD format."}, status=400)

        latest_date = HistoricalMarketPrice.objects.order_by("-price_date").values_list("price_date", flat=True).first()
        source_version = HistoricalMarketPrice.objects.order_by("-source_version").values_list("source_version", flat=True).first()
        try:
            page_size = min(max(int(request.query_params.get("page_size", 50)), 1), 100)
            page = max(int(request.query_params.get("page", 1)), 1)
        except ValueError:
            return Response({"detail": "Page and page size must be positive integers."}, status=400)
        total = queryset.count()
        offset = (page - 1) * page_size
        rows = queryset[offset:offset + page_size]
        results = [{
            "id": row.id, "region": row.region, "district": row.district, "market": row.market,
            "latitude": row.latitude, "longitude": row.longitude, "price_date": row.price_date,
            "crop": row.crop, "currency": row.currency, "unit": row.unit,
            "opening_price": row.opening_price, "high_price": row.high_price,
            "low_price": row.low_price, "closing_price": row.closing_price,
            "trust_score": row.trust_score, "data_coverage": row.data_coverage,
            "recent_data_coverage": row.recent_data_coverage,
            "index_confidence_score": row.index_confidence_score,
            "spatially_interpolated": row.spatially_interpolated,
        } for row in rows]
        stale = latest_date is None or latest_date < (timezone.localdate() - timedelta(days=90))
        return Response({
            "results": results,
            "pagination": {"page": page, "page_size": page_size, "total": total, "has_next": offset + page_size < total},
            "coverage": {
                "crops": list(HistoricalMarketPrice.objects.values_list("crop", flat=True).distinct().order_by("crop")),
                "markets": HistoricalMarketPrice.objects.values("geo_id").distinct().count(),
                "earliest_date": HistoricalMarketPrice.objects.order_by("price_date").values_list("price_date", flat=True).first(),
                "latest_date": latest_date,
            },
            "source": {
                "name": "World Bank Microdata Library",
                "dataset": "MWI_2021_RTFP_v02_M",
                "version": source_version,
                "url": "https://microdata.worldbank.org/catalog/6171",
                "doi": "https://doi.org/10.48529/2ZH0-JF55",
            },
            "methodology_notice": "Monthly modelled estimates; some values are spatially interpolated. These are not live quotes or guaranteed transaction prices.",
            "stale": stale,
            "stale_after_days": 90,
        })

def diagnosis_payload(row):
    return {
        "id": row.id, "provider": "Kindwise Crop.health", "crop": row.crop,
        "results": row.results, "status": row.status, "consented_at": row.consented_at,
        "created_at": row.created_at, "deleted_at": row.deleted_at,
        "warning": DISCLAIMER, "expert_escalated": row.escalations.exists(),
        "harmful_reported": row.reports.filter(category__in=["harmful_advice", "unsafe_pesticide"]).exists(),
    }

class CropDiagnosisListCreate(APIView):
    def get(self, request):
        rows = request.user.crop_diagnoses.prefetch_related("reports", "escalations").order_by("-created_at")[:100]
        return Response({"results": [diagnosis_payload(row) for row in rows], "supported_crops": SUPPORTED_CROPS, "warning": DISCLAIMER})

    @transaction.atomic
    def post(self, request):
        if request.data.get("consent") not in [True, "true", "True", "1", 1]:
            return Response({"detail": "Explicit consent is required before the photograph can be transferred to Kindwise Crop.health."}, status=400)
        if request.data.get("consent_version") != CONSENT_VERSION:
            return Response({"detail": "Please review and accept the current image-processing consent notice."}, status=400)
        upload = request.FILES.get("image")
        try:
            cleaned, image_sha256 = prepare_image(upload)
            reference, results = identify(cleaned)
        except DiagnosisError as exc:
            return Response({"detail": str(exc)}, status=503 if "provider" in str(exc).lower() or "configured" in str(exc).lower() else 400)
        crop = str(request.data.get("crop", "")).strip()[:80]
        row = CropDiagnosis.objects.create(
            user=request.user, provider_reference=reference, crop=crop,
            image_sha256=image_sha256, original_filename=str(getattr(upload, "name", ""))[:180],
            consent_version=CONSENT_VERSION, consented_at=timezone.now(), results=results,
        )
        AuditLog.objects.create(actor=request.user, action="diagnosis.created", target_type="crop_diagnosis", target_id=str(row.id), metadata={"provider": "kindwise_crop_health", "consent_version": CONSENT_VERSION, "image_retained": False})
        return Response(diagnosis_payload(row), status=201)

class CropDiagnosisDetail(APIView):
    def get(self, request, diagnosis_id):
        row = generics.get_object_or_404(CropDiagnosis.objects.prefetch_related("reports", "escalations"), id=diagnosis_id, user=request.user)
        return Response(diagnosis_payload(row))

    @transaction.atomic
    def delete(self, request, diagnosis_id):
        row = generics.get_object_or_404(CropDiagnosis.objects.select_for_update(), id=diagnosis_id, user=request.user)
        remote_deleted = False
        try: remote_deleted = delete_remote(row.provider_reference)
        except DiagnosisError: pass
        row.results = {}; row.crop = ""; row.original_filename = ""; row.image_sha256 = ""
        row.remote_deleted = remote_deleted; row.deleted_at = timezone.now()
        row.status = "deleted" if remote_deleted else "deletion_pending"
        row.save(update_fields=["results", "crop", "original_filename", "image_sha256", "remote_deleted", "deleted_at", "status"])
        AuditLog.objects.create(actor=request.user, action="diagnosis.deleted", target_type="crop_diagnosis", target_id=str(row.id), metadata={"remote_deleted": remote_deleted})
        return Response({"id": row.id, "status": row.status, "remote_deleted": remote_deleted}, status=200 if remote_deleted else 202)

class CropDiagnosisReportView(APIView):
    def post(self, request, diagnosis_id):
        row = generics.get_object_or_404(CropDiagnosis, id=diagnosis_id, user=request.user, deleted_at__isnull=True)
        category = str(request.data.get("category", "")).strip()
        details = str(request.data.get("details", "")).strip()
        if category not in dict(DiagnosisReport.CATEGORIES) or len(details) < 10 or len(details) > 2000:
            return Response({"detail": "Choose a report category and provide between 10 and 2,000 characters."}, status=400)
        report = DiagnosisReport.objects.create(diagnosis=row, reporter=request.user, category=category, details=details)
        AuditLog.objects.create(actor=request.user, action="diagnosis.reported", target_type="crop_diagnosis", target_id=str(row.id), metadata={"report_id": report.id, "category": category})
        return Response({"id": report.id, "status": report.status}, status=201)

class CropDiagnosisEscalationView(APIView):
    def post(self, request, diagnosis_id):
        row = generics.get_object_or_404(CropDiagnosis, id=diagnosis_id, user=request.user, deleted_at__isnull=True)
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10 or len(reason) > 2000:
            return Response({"detail": "Explain the concern in between 10 and 2,000 characters."}, status=400)
        escalation = DiagnosisEscalation.objects.create(diagnosis=row, requested_by=request.user, reason=reason)
        Notification.objects.create(user=request.user, type="advisory", title="Expert review requested", message="Your crop diagnosis has been queued for human review.", action_url=f"/app/advisory/pest-detection?diagnosis={row.id}")
        AuditLog.objects.create(actor=request.user, action="diagnosis.escalated", target_type="crop_diagnosis", target_id=str(row.id), metadata={"escalation_id": escalation.id})
        return Response({"id": escalation.id, "status": escalation.status}, status=201)

class TraceabilityBatchListCreate(generics.ListCreateAPIView):
    serializer_class = TraceabilityBatchSerializer
    def get_queryset(self): return self.request.user.traceability_batches.prefetch_related("events").order_by("-created_at")
    def perform_create(self, serializer): serializer.save(owner=self.request.user)

class TraceabilityBatchDetail(generics.RetrieveAPIView):
    serializer_class = TraceabilityBatchSerializer
    def get_queryset(self): return self.request.user.traceability_batches.prefetch_related("events")

class TraceabilityEventCreate(APIView):
    def post(self, request, batch_id):
        batch = generics.get_object_or_404(TraceabilityBatch, id=batch_id, owner=request.user)
        stage = str(request.data.get("stage", "")).strip()
        description = str(request.data.get("description", "")).strip()
        location = str(request.data.get("location", "")).strip()
        event_type = str(request.data.get("event_type", stage)).strip()
        unit = str(request.data.get("unit", "")).strip().lower()
        try: quantity = __import__("decimal").Decimal(str(request.data.get("quantity")))
        except __import__("decimal").InvalidOperation: return Response({"detail": "Enter a valid event quantity."}, status=400)
        if not stage or len(stage) > 80:
            return Response({"detail": "Stage is required and must not exceed 80 characters."}, status=400)
        if not description or len(description) > 2000:
            return Response({"detail": "Description is required and must not exceed 2,000 characters."}, status=400)
        if len(location) > 140:
            return Response({"detail": "Location must not exceed 140 characters."}, status=400)
        if not event_type or len(event_type) > 80 or quantity <= 0 or not unit or len(unit) > 24: return Response({"detail": "Event type, positive quantity and unit are required."}, status=400)
        uploads = request.FILES.getlist("evidence")
        if len(uploads) > 5 or any(upload.size > 5 * 1024 * 1024 or upload.content_type not in ["image/jpeg", "image/png", "application/pdf"] for upload in uploads): return Response({"detail": "Attach up to five JPG, PNG or PDF files, each no larger than 5 MB."}, status=400)
        corrects = None
        if request.data.get("corrects"):
            corrects = generics.get_object_or_404(TraceabilityEvent, id=request.data.get("corrects"), batch=batch)
        event = append_event(batch_id=batch.id, actor=request.user, event_type=event_type, stage=stage, description=description, location=location, quantity=quantity, unit=unit, uploads=uploads, corrects=corrects)
        return Response({"id": event.id, "status": batch.status, "event_hash": event.event_hash}, status=201)

class TraceabilityEventVerification(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def post(self, request, event_id):
        event = generics.get_object_or_404(TraceabilityEvent.objects.select_for_update(), id=event_id)
        decision, reason = request.data.get("decision"), str(request.data.get("reason", "")).strip()
        if decision not in ["verified", "rejected"] or (decision == "rejected" and len(reason) < 5): return Response({"detail": "Choose verified or rejected and provide a rejection reason."}, status=400)
        before = {"verification_status": event.verification_status}
        event.verification_status, event.verified_by, event.verified_at = decision, request.user, timezone.now(); event.save(update_fields=["verification_status", "verified_by", "verified_at"])
        TraceabilityAudit.objects.create(batch=event.batch, event=event, actor=request.user, action=f"event.{decision}", reason=reason, before=before, after={"verification_status": decision})
        return Response({"id": event.id, "verification_status": decision})

class TraceabilityAuditList(APIView):
    def get(self, request, batch_id):
        batch = generics.get_object_or_404(TraceabilityBatch, id=batch_id, owner=request.user)
        rows = batch.audit_history.select_related("actor").order_by("created_at")
        return Response([{"id": row.id, "event_id": row.event_id, "actor": row.actor.username, "action": row.action, "reason": row.reason, "before": row.before, "after": row.after, "created_at": row.created_at} for row in rows])

class TraceabilityVerify(APIView):
    permission_classes = [permissions.AllowAny]; authentication_classes = []
    def get(self, request, batch_code):
        batch = generics.get_object_or_404(TraceabilityBatch.objects.prefetch_related("events"), batch_code=batch_code)
        return Response(TraceabilityBatchSerializer(batch, context={"request": request, "public": True}).data)

class SmartContractListCreate(generics.ListCreateAPIView):
    serializer_class = EmptySchemaSerializer
    def get(self, request): return Response(list(request.user.smart_contracts.values("id", "name", "terms", "status", "created_at")))
    def post(self, request):
        contract = SmartContract.objects.create(owner=request.user, name=request.data.get("name", "Agricultural agreement"), terms=request.data.get("terms", {}))
        return Response({"id": contract.id, "status": contract.status}, status=201)

class AdminOverview(APIView):
    permission_classes = [IsAdmin]
    def get(self, request): return Response({"users": User.objects.count(), "activeListings": Listing.objects.filter(is_active=True).count(), "orders": Order.objects.count(), "volume": Order.objects.aggregate(value=Sum("total"))["value"] or 0, "disputes": Dispute.objects.filter(status="open").count(), "usersByType": list(User.objects.values("account_type").annotate(value=Count("id"))), "revenueTrend": list(Order.objects.annotate(period=TruncMonth("created_at")).values("period").annotate(value=Sum("total"), orders=Count("id")).order_by("period")), "categories": list(Listing.objects.values("category").annotate(value=Count("id")).order_by("-value"))})

class AdminUsers(generics.ListAPIView):
    permission_classes = [IsAdmin]; serializer_class = UserSerializer; pagination_class = StandardPagination
    def get_queryset(self):
        queryset = User.objects.order_by("-date_joined")
        state = self.request.query_params.get("state")
        if state == "suspended": queryset = queryset.filter(is_active=False)
        if state == "active": queryset = queryset.filter(is_active=True)
        return queryset

class AdminSettingsView(APIView):
    permission_classes = [IsAdmin]
    def get(self, request): return Response({row.key: row.value for row in PlatformSetting.objects.all()})
    def put(self, request):
        reason = str(request.data.get("change_reason", "")).strip()
        changes = {key: value for key, value in request.data.items() if key != "change_reason"}
        if not changes or len(reason) < 5: return Response({"detail": "A change reason of at least five characters is required."}, status=400)
        for key, value in changes.items():
            setting, _ = PlatformSetting.objects.get_or_create(key=key, defaults={"value": None})
            before = snapshot(setting, ["key", "value"]); setting.value = value; setting.save(update_fields=["value", "updated_at"])
            audit_change(actor=request.user, action="configuration.changed", target=setting, before=before, after=snapshot(setting, ["key", "value"]), reason=reason)
        return self.get(request)

class AdminListingApprovals(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = Listing.objects.filter(approval_status=request.query_params.get("status", "pending")).select_related("seller").order_by("created_at")
        return Response([{"id": row.id, "name": row.name, "seller": row.seller.username, "price": row.price, "quantity": row.quantity, "category": row.category, "approval_status": row.approval_status, "created_at": row.created_at} for row in rows])
    @transaction.atomic
    def post(self, request):
        listing = generics.get_object_or_404(Listing.objects.select_for_update(), id=request.data.get("listing_id"))
        decision, reason = request.data.get("decision"), str(request.data.get("reason", "")).strip()
        if decision not in ["approved", "rejected", "suspended"] or len(reason) < 5: return Response({"detail": "Decision and reason of at least five characters are required."}, status=400)
        fields = ["approval_status", "moderation_reason", "is_active", "moderated_by"]
        before = snapshot(listing, fields)
        listing.approval_status, listing.moderation_reason, listing.moderated_by, listing.moderated_at = decision, reason, request.user, timezone.now()
        listing.is_active = decision == "approved"
        listing.save(update_fields=["approval_status", "moderation_reason", "moderated_by", "moderated_at", "is_active"])
        audit_change(actor=request.user, action=f"listing.{decision}", target=listing, before=before, after=snapshot(listing, fields), reason=reason)
        return Response({"id": listing.id, "approval_status": decision})

class AdminUserAction(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def post(self, request, user_id):
        user = generics.get_object_or_404(User.objects.select_for_update(), id=user_id)
        action, reason = request.data.get("action"), str(request.data.get("reason", "")).strip()
        if user == request.user: return Response({"detail": "You cannot change your own administrative access here."}, status=400)
        if action not in ["suspend", "reinstate"] or len(reason) < 5: return Response({"detail": "Action and reason of at least five characters are required."}, status=400)
        before = snapshot(user, ["is_active"]); user.is_active = action == "reinstate"; user.save(update_fields=["is_active"])
        audit_change(actor=request.user, action=f"user.{action}", target=user, before=before, after=snapshot(user, ["is_active"]), reason=reason)
        return Response({"id": user.id, "is_active": user.is_active})

class AdminRoleAssignment(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def post(self, request, user_id):
        user = generics.get_object_or_404(User.objects.select_for_update(), id=user_id)
        role, reason = request.data.get("role"), str(request.data.get("reason", "")).strip()
        if user == request.user or role not in dict(User.USER_TYPES) or len(reason) < 5: return Response({"detail": "Valid role, separate target user and reason are required."}, status=400)
        before = snapshot(user, ["user_type", "is_staff"]); user.user_type, user.is_staff = role, role == "admin"; user.save(update_fields=["user_type", "is_staff"])
        audit_change(actor=request.user, action="user.role_assigned", target=user, before=before, after=snapshot(user, ["user_type", "is_staff"]), reason=reason)
        return Response({"id": user.id, "user_type": user.user_type, "is_staff": user.is_staff})

class AdminFees(APIView):
    permission_classes = [IsAdmin]
    defaults = {"platform_percent": "0", "withdrawal_percent": "0", "minimum_fee": "0"}
    def get(self, request):
        setting, _ = PlatformSetting.objects.get_or_create(key="fee_configuration", defaults={"value": self.defaults})
        return Response({**self.defaults, **(setting.value or {})})
    def put(self, request):
        reason = str(request.data.get("reason", "")).strip()
        try: values = {key: str(__import__("decimal").Decimal(str(request.data.get(key, 0)))) for key in self.defaults}
        except __import__("decimal").InvalidOperation: return Response({"detail": "Fees must be valid numbers."}, status=400)
        if len(reason) < 5 or any(__import__("decimal").Decimal(value) < 0 for value in values.values()) or any(__import__("decimal").Decimal(values[key]) > 100 for key in ["platform_percent", "withdrawal_percent"]): return Response({"detail": "Provide a reason; percentages must be between 0 and 100."}, status=400)
        setting, _ = PlatformSetting.objects.get_or_create(key="fee_configuration", defaults={"value": self.defaults}); before = snapshot(setting, ["key", "value"]); setting.value = values; setting.save(update_fields=["value", "updated_at"])
        audit_change(actor=request.user, action="fees.changed", target=setting, before=before, after=snapshot(setting, ["key", "value"]), reason=reason)
        return Response(values)

class AdminDataExport(APIView):
    permission_classes = [IsAdmin]
    def get(self, request, dataset):
        definitions = {"users": (User.objects.all(), ["id", "username", "email", "user_type", "account_type", "is_active", "date_joined"]), "listings": (Listing.objects.all(), ["id", "seller_id", "name", "category", "price", "quantity", "approval_status", "created_at"]), "orders": (Order.objects.all(), ["id", "buyer_id", "status", "total", "payment_method", "created_at"]), "audit": (AuditLog.objects.all(), ["id", "actor_id", "action", "target_type", "target_id", "created_at"])}
        if dataset not in definitions: return Response({"detail": "Unknown export dataset."}, status=404)
        queryset, fields = definitions[dataset]; response = HttpResponse(content_type="text/csv"); response["Content-Disposition"] = f'attachment; filename="mlimiconnect-{dataset}.csv"'; writer = csv.writer(response); writer.writerow(fields)
        for row in queryset.values_list(*fields).iterator(): writer.writerow(row)
        AuditLog.objects.create(actor=request.user, action="data.exported", target_type="dataset", target_id=dataset, metadata={"row_count": queryset.count()})
        return response

class AdminApprovalList(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = Organization.objects.filter(verification_status="pending").select_related("owner").order_by("created_at")
        return Response([{"id": row.id, "legal_name": row.legal_name, "registration_number": row.registration_number, "owner": row.owner.username, "created_at": row.created_at} for row in rows])

class AdminApprovalDecision(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def post(self, request, organization_id):
        organization = generics.get_object_or_404(Organization.objects.select_for_update(), id=organization_id)
        decision = request.data.get("decision")
        reason = str(request.data.get("reason", "")).strip()
        if decision not in ["verified", "rejected"] or (decision == "rejected" and len(reason) < 5):
            return Response({"detail": "Choose verified or rejected and provide a rejection reason."}, status=400)
        before = snapshot(organization, ["verification_status"])
        organization.verification_status = decision; organization.save(update_fields=["verification_status"])
        audit_change(actor=request.user, action=f"organization.{decision}", target=organization, before=before, after=snapshot(organization, ["verification_status"]), reason=reason)
        return Response({"id": organization.id, "verification_status": decision})

class AdminDisputeList(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = Dispute.objects.select_related("order__buyer", "opened_by").order_by("-created_at")
        return Response([{"id": row.id, "order_id": row.order_id, "buyer": row.order.buyer.username, "reason": row.reason, "evidence": row.evidence, "status": row.status, "total": row.order.total, "created_at": row.created_at} for row in rows])

class AdminDisputeDecision(APIView):
    permission_classes = [IsAdmin]
    @transaction.atomic
    def post(self, request, dispute_id):
        dispute = generics.get_object_or_404(Dispute.objects.select_for_update().select_related("order"), id=dispute_id, status="open")
        decision, note = request.data.get("decision"), str(request.data.get("note", "")).strip()
        if decision not in ["refund", "reject"] or len(note) < 10: return Response({"detail": "A valid decision and a note of at least 10 characters are required."}, status=400)
        before = snapshot(dispute, ["status", "decision", "resolution_note", "refund_amount", "decided_by"])
        amount = dispute.order.total if decision == "refund" else None
        dispute.status, dispute.decision, dispute.resolution_note, dispute.refund_amount, dispute.decided_by, dispute.decided_at = "resolved", decision, note, amount, request.user, timezone.now()
        dispute.save(update_fields=["status", "decision", "resolution_note", "refund_amount", "decided_by", "decided_at"])
        if decision == "refund": PaymentReconciliation.objects.create(order=dispute.order, provider="manual_admin", provider_reference=f"REFUND-{dispute.id}", expected_amount=amount, settled_amount=amount, status="refunded", reconciled_by=request.user, reconciled_at=timezone.now())
        audit_change(actor=request.user, action=f"dispute.{decision}", target=dispute, before=before, after=snapshot(dispute, ["status", "decision", "resolution_note", "refund_amount", "decided_by"]), reason=note, extra={"amount": str(amount or 0)})
        return Response({"id": dispute.id, "status": dispute.status, "decision": decision})

class TransporterProfileView(APIView):
    def get(self, request):
        profile = getattr(request.user, "transporter_profile", None)
        return Response(None if not profile else {"vehicle_type": profile.vehicle_type, "capacity_kg": profile.capacity_kg, "license_reference": profile.license_reference, "verification_status": profile.verification_status})
    def put(self, request):
        try: capacity = int(request.data.get("capacity_kg"))
        except (TypeError, ValueError): return Response({"detail": "Capacity must be a positive number of kilograms."}, status=400)
        vehicle, license_ref = str(request.data.get("vehicle_type", "")).strip(), str(request.data.get("license_reference", "")).strip()
        if capacity <= 0 or not vehicle or not license_ref: return Response({"detail": "Vehicle, capacity and licence reference are required."}, status=400)
        profile, _ = TransporterProfile.objects.update_or_create(user=request.user, defaults={"vehicle_type": vehicle, "capacity_kg": capacity, "license_reference": license_ref, "verification_status": "pending"})
        return Response({"verification_status": profile.verification_status}, status=201)

class DeliveryList(APIView):
    def get(self, request):
        rows = Delivery.objects.filter(transporter=request.user).select_related("order").order_by("-created_at")
        return Response([{"id": row.id, "order_id": row.order_id, "pickup_location": row.pickup_location, "delivery_location": row.delivery_location, "status": row.status} for row in rows])

class DeliveryStatus(APIView):
    @transaction.atomic
    def patch(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery.objects.select_for_update(), id=delivery_id, transporter=request.user)
        next_status = request.data.get("status")
        allowed = {"assigned": ["picked_up"], "picked_up": ["delivered", "failed_delivery"], "failed_delivery": ["picked_up"]}
        if next_status not in allowed.get(delivery.status, []):
            return Response({"detail": "Invalid delivery status transition."}, status=400)
        required = "pickup" if next_status == "picked_up" else "delivery" if next_status == "delivered" else "failed_delivery"
        if not delivery.order.delivery_evidence.filter(evidence_type=required).exists(): return Response({"detail": f"{required.replace('_', ' ').title()} evidence is required."}, status=400)
        delivery.status = next_status
        if next_status == "picked_up": delivery.picked_up_at = timezone.now()
        if next_status == "delivered": delivery.delivered_at = timezone.now()
        if next_status == "failed_delivery": delivery.failure_reason = str(request.data.get("reason", "")).strip()
        if next_status == "failed_delivery" and len(delivery.failure_reason) < 5: return Response({"detail": "A failed-delivery reason is required."}, status=400)
        delivery.save(update_fields=["status", "picked_up_at", "delivered_at", "failure_reason"])
        if next_status == "delivered": transition_order(delivery.order_id, request.user, "delivered", request.data.get("reason", "Delivery evidence recorded."))
        if next_status == "failed_delivery": transition_order(delivery.order_id, request.user, "failed_delivery", delivery.failure_reason)
        return Response({"id": delivery.id, "status": delivery.status})

class TransporterDocumentsView(APIView):
    def get(self, request):
        profile = generics.get_object_or_404(TransporterProfile, user=request.user)
        return Response([{"id": row.id, "document_type": row.document_type, "file": row.file.url, "verification_status": row.verification_status, "created_at": row.created_at} for row in profile.documents.order_by("-created_at")])
    def post(self, request):
        profile = generics.get_object_or_404(TransporterProfile, user=request.user)
        upload = request.FILES.get("file")
        if not upload or upload.size > 5 * 1024 * 1024 or upload.content_type not in ["image/jpeg", "image/png", "application/pdf"]: return Response({"detail": "Upload a JPG, PNG or PDF no larger than 5 MB."}, status=400)
        row = TransporterDocument.objects.create(profile=profile, document_type=str(request.data.get("document_type", "other"))[:40], file=upload)
        return Response({"id": row.id, "verification_status": row.verification_status}, status=201)

class DeliveryRequestView(APIView):
    def get(self, request):
        order = generics.get_object_or_404(Order, id=request.query_params.get("order_id"), buyer=request.user)
        delivery = Delivery.objects.select_related("transporter", "order").filter(order=order).first()
        if not delivery:
            return Response(None)
        return Response({"id": delivery.id, "order_id": delivery.order_id, "pickup_location": delivery.pickup_location, "delivery_location": delivery.delivery_location, "pickup_latitude": delivery.pickup_latitude, "pickup_longitude": delivery.pickup_longitude, "delivery_latitude": delivery.delivery_latitude, "delivery_longitude": delivery.delivery_longitude, "status": delivery.status, "distance_km": delivery.distance_km, "delivery_fee": delivery.delivery_fee, "liability_rule": delivery.liability_rule, "transporter": delivery.transporter.username if delivery.transporter else None, "external_provider": delivery.external_provider, "external_reference": delivery.external_reference, "quotes": [{"id": row.id, "transporter": row.transporter.username, "amount": row.amount, "estimated_hours": row.estimated_hours, "note": row.note, "status": row.status} for row in delivery.quotes.select_related("transporter").order_by("amount")], "locations": [{"latitude": row.latitude, "longitude": row.longitude, "status_note": row.status_note, "created_at": row.created_at} for row in delivery.location_updates.order_by("-created_at")[:20]]})
    @transaction.atomic
    def post(self, request):
        order = generics.get_object_or_404(Order.objects.select_for_update(), id=request.data.get("order_id"), buyer=request.user, status__in=["paid", "accepted", "packed"])
        pickup_selection = delivery_selection = None
        if request.data.get("pickup_selection_token") or request.data.get("delivery_selection_token"):
            try: pickup_selection, delivery_selection = read_selection(request.data.get("pickup_selection_token")), read_selection(request.data.get("delivery_selection_token"))
            except GeocodingError as error: return Response({"detail": str(error)}, status=400)
        if pickup_selection and delivery_selection:
            plat, plon, dlat, dlon = map(float, [pickup_selection["latitude"], pickup_selection["longitude"], delivery_selection["latitude"], delivery_selection["longitude"]])
            delta_lat, delta_lon = math.radians(dlat - plat), math.radians(dlon - plon)
            a = math.sin(delta_lat / 2) ** 2 + math.cos(math.radians(plat)) * math.cos(math.radians(dlat)) * math.sin(delta_lon / 2) ** 2
            distance = Decimal(str(round(6371 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)))
        else:
            try: distance = Decimal(str(request.data.get("distance_km", 0)))
            except InvalidOperation: return Response({"detail": "Distance must be a valid number."}, status=400)
        if distance < 0: return Response({"detail": "Distance cannot be negative."}, status=400)
        fees, _ = PlatformSetting.objects.get_or_create(key="logistics_fee_configuration", defaults={"value": {"base_fee": "2000", "per_km": "1000"}})
        base, per_km = Decimal(str(fees.value.get("base_fee", 2000))), Decimal(str(fees.value.get("per_km", 1000)))
        fee = base + distance * per_km
        delivery, created = Delivery.objects.update_or_create(order=order, defaults={"pickup_location": str((pickup_selection or {}).get("label") or request.data.get("pickup_location", ""))[:180], "delivery_location": str((delivery_selection or {}).get("label") or request.data.get("delivery_location", ""))[:180], "pickup_latitude": (pickup_selection or {}).get("latitude"), "pickup_longitude": (pickup_selection or {}).get("longitude"), "delivery_latitude": (delivery_selection or {}).get("latitude"), "delivery_longitude": (delivery_selection or {}).get("longitude"), "pickup_osm_reference": (pickup_selection or {}).get("osm_reference", ""), "delivery_osm_reference": (delivery_selection or {}).get("osm_reference", ""), "status": "open_for_quotes", "distance_km": distance, "delivery_fee": fee, "liability_rule": str(request.data.get("liability_rule", "Transporter is liable for documented loss or damage while goods are in their custody."))})
        if not delivery.pickup_location or not delivery.delivery_location: return Response({"detail": "Pickup and delivery locations are required."}, status=400)
        audit_change(actor=request.user, action="delivery.quotes_opened", target=delivery, before={}, after=snapshot(delivery, ["status", "distance_km", "delivery_fee", "liability_rule"]), reason="Delivery quotation requested.")
        return Response({"id": delivery.id, "status": delivery.status, "calculated_fee": delivery.delivery_fee, "liability_rule": delivery.liability_rule}, status=201 if created else 200)

class GeocodingSearchView(APIView):
    throttle_classes = [GeocodingThrottle]
    def get(self, request):
        try: results, cached = search_malawi(request.query_params.get("q"), request.query_params.get("language", "en"))
        except GeocodingRateLimited as error: return Response({"detail": str(error)}, status=429, headers={"Retry-After": "1"})
        except GeocodingError as error: return Response({"detail": str(error)}, status=503 if "unavailable" in str(error).lower() else 400)
        return Response({"results": [{**row, "selection_token": sign_selection(row)} for row in results], "cached": cached, "country": "Malawi", "attribution": ATTRIBUTION, "attribution_url": ATTRIBUTION_URL, "notice": "Search only after the user submits. Do not enter confidential or highly personal information."})

class DeliveryQuotesView(APIView):
    def get(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id)
        if delivery.order.buyer_id != request.user.id and delivery.transporter_id != request.user.id and not request.user.is_staff: raise PermissionDenied("You cannot inspect these quotations.")
        return Response([{"id": row.id, "transporter_id": row.transporter_id, "transporter": row.transporter.username, "amount": row.amount, "estimated_hours": row.estimated_hours, "note": row.note, "status": row.status, "created_at": row.created_at} for row in delivery.quotes.select_related("transporter").order_by("amount")])
    def post(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id, status="open_for_quotes")
        profile = generics.get_object_or_404(TransporterProfile, user=request.user, verification_status="verified")
        try: amount, hours = Decimal(str(request.data.get("amount"))), int(request.data.get("estimated_hours"))
        except (InvalidOperation, TypeError, ValueError): return Response({"detail": "Valid quotation amount and estimated hours are required."}, status=400)
        if amount <= 0 or hours <= 0: return Response({"detail": "Quotation amount and hours must be positive."}, status=400)
        quote, created = DeliveryQuote.objects.update_or_create(delivery=delivery, transporter=request.user, defaults={"amount": amount, "estimated_hours": hours, "note": str(request.data.get("note", "")), "status": "pending"})
        return Response({"id": quote.id, "status": quote.status}, status=201 if created else 200)
    @transaction.atomic
    def patch(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery.objects.select_for_update(), id=delivery_id, order__buyer=request.user, status="open_for_quotes")
        quote = generics.get_object_or_404(DeliveryQuote, id=request.data.get("quote_id"), delivery=delivery, status="pending")
        if not request.data.get("accept_liability"): return Response({"detail": "The delivery liability rule must be accepted."}, status=400)
        delivery.transporter, delivery.delivery_fee, delivery.status, delivery.assigned_at, delivery.liability_accepted_at = quote.transporter, quote.amount, "assigned", timezone.now(), timezone.now(); delivery.save(update_fields=["transporter", "delivery_fee", "status", "assigned_at", "liability_accepted_at"])
        quote.status = "accepted"; quote.save(update_fields=["status"]); delivery.quotes.exclude(id=quote.id).update(status="rejected")
        audit_change(actor=request.user, action="delivery.quote_accepted", target=delivery, before={"status": "open_for_quotes"}, after=snapshot(delivery, ["status", "transporter", "delivery_fee", "liability_accepted_at"]), reason="Buyer accepted transporter quotation.")
        return Response({"id": delivery.id, "status": delivery.status, "transporter": quote.transporter.username, "delivery_fee": delivery.delivery_fee})

class DeliveryLocationView(APIView):
    def get(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id)
        if request.user.id not in [delivery.order.buyer_id, delivery.transporter_id] and not request.user.is_staff: raise PermissionDenied("You cannot view this delivery location.")
        return Response([{"latitude": row.latitude, "longitude": row.longitude, "accuracy_m": row.accuracy_m, "status_note": row.status_note, "created_at": row.created_at} for row in delivery.location_updates.order_by("-created_at")[:100]])
    def post(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id, transporter=request.user, status__in=["assigned", "picked_up", "failed_delivery"])
        try: latitude, longitude = Decimal(str(request.data.get("latitude"))), Decimal(str(request.data.get("longitude")))
        except InvalidOperation: return Response({"detail": "Valid coordinates are required."}, status=400)
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180): return Response({"detail": "Coordinates are outside the valid range."}, status=400)
        row = DeliveryLocationUpdate.objects.create(delivery=delivery, created_by=request.user, latitude=latitude, longitude=longitude, accuracy_m=request.data.get("accuracy_m") or None, status_note=str(request.data.get("status_note", ""))[:180])
        return Response({"id": row.id, "created_at": row.created_at}, status=201)

class DeliveryRatingView(APIView):
    def post(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id, order__buyer=request.user, status="delivered")
        try: score = int(request.data.get("score"))
        except (TypeError, ValueError): return Response({"detail": "Rating must be from 1 to 5."}, status=400)
        if score not in range(1, 6): return Response({"detail": "Rating must be from 1 to 5."}, status=400)
        rating, created = DeliveryRating.objects.update_or_create(delivery=delivery, defaults={"buyer": request.user, "score": score, "comment": str(request.data.get("comment", ""))[:1000]})
        return Response({"id": rating.id, "score": rating.score}, status=201 if created else 200)

class TransporterDashboardView(APIView):
    def get(self, request):
        profile = generics.get_object_or_404(TransporterProfile, user=request.user)
        deliveries = Delivery.objects.filter(transporter=request.user).select_related("order").order_by("-created_at")
        open_jobs = Delivery.objects.filter(status="open_for_quotes").exclude(quotes__transporter=request.user).order_by("-created_at")[:50]
        ratings = DeliveryRating.objects.filter(delivery__transporter=request.user)
        return Response({"profile": {"verification_status": profile.verification_status, "vehicle_type": profile.vehicle_type, "capacity_kg": profile.capacity_kg, "documents": profile.documents.count()}, "summary": {"assigned": deliveries.filter(status="assigned").count(), "in_transit": deliveries.filter(status="picked_up").count(), "completed": deliveries.filter(status="delivered").count(), "average_rating": ratings.aggregate(value=__import__("django.db.models", fromlist=["Avg"]).Avg("score"))["value"]}, "deliveries": [{"id": row.id, "order_id": row.order_id, "pickup_location": row.pickup_location, "delivery_location": row.delivery_location, "status": row.status, "delivery_fee": row.delivery_fee, "liability_rule": row.liability_rule} for row in deliveries[:100]], "open_jobs": [{"id": row.id, "order_id": row.order_id, "pickup_location": row.pickup_location, "delivery_location": row.delivery_location, "distance_km": row.distance_km, "calculated_fee": row.delivery_fee} for row in open_jobs]})

class ExternalLogisticsDispatchView(APIView):
    permission_classes = [IsAdmin]
    def post(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id)
        try: reference, payload = create_external_shipment(delivery)
        except ProviderUnavailable as error: return Response({"detail": str(error)}, status=503)
        before = snapshot(delivery, ["external_provider", "external_reference"]); delivery.external_provider, delivery.external_reference = settings.LOGISTICS_PROVIDER, reference; delivery.save(update_fields=["external_provider", "external_reference"])
        audit_change(actor=request.user, action="delivery.external_dispatched", target=delivery, before=before, after=snapshot(delivery, ["external_provider", "external_reference"]), reason=str(request.data.get("reason", "External logistics shipment created.")), extra={"provider_response": payload})
        return Response({"external_provider": delivery.external_provider, "external_reference": reference})

class AdminTransporters(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = TransporterProfile.objects.select_related("user").order_by("-created_at")
        return Response([{"id": row.id, "user": row.user.username, "vehicle_type": row.vehicle_type, "capacity_kg": row.capacity_kg, "license_reference": row.license_reference, "verification_status": row.verification_status} for row in rows])
    def post(self, request):
        profile = generics.get_object_or_404(TransporterProfile, id=request.data.get("profile_id"))
        decision = request.data.get("decision")
        if decision not in ["verified", "rejected", "suspended"]: return Response({"detail": "Invalid transporter decision."}, status=400)
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 5: return Response({"detail": "Provide a reason of at least five characters."}, status=400)
        before = snapshot(profile, ["verification_status"])
        profile.verification_status = decision; profile.save(update_fields=["verification_status"])
        audit_change(actor=request.user, action=f"transporter.{decision}", target=profile, before=before, after=snapshot(profile, ["verification_status"]), reason=reason)
        return Response({"id": profile.id, "verification_status": decision})

class AdminReconciliations(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = PaymentReconciliation.objects.select_related("order").order_by("-created_at")
        return Response([{"id": row.id, "order_id": row.order_id, "provider": row.provider, "provider_reference": row.provider_reference, "expected_amount": row.expected_amount, "settled_amount": row.settled_amount, "status": row.status, "created_at": row.created_at} for row in rows])

class AdminDeliveries(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = Delivery.objects.select_related("order", "transporter").order_by("-created_at")
        return Response([{"id": row.id, "order_id": row.order_id, "transporter": row.transporter.username if row.transporter else None, "pickup_location": row.pickup_location, "delivery_location": row.delivery_location, "status": row.status} for row in rows])
    @transaction.atomic
    def post(self, request):
        order = generics.get_object_or_404(Order, id=request.data.get("order_id"), status="paid")
        transporter = generics.get_object_or_404(User, id=request.data.get("transporter_id"), transporter_profile__verification_status="verified")
        pickup, destination = str(request.data.get("pickup_location", "")).strip(), str(request.data.get("delivery_location", "")).strip()
        if not pickup or not destination: return Response({"detail": "Pickup and delivery locations are required."}, status=400)
        existing = Delivery.objects.filter(order=order).first()
        before = snapshot(existing, ["transporter", "pickup_location", "delivery_location", "status"]) if existing else {}
        delivery, created = Delivery.objects.update_or_create(order=order, defaults={"transporter": transporter, "pickup_location": pickup, "delivery_location": destination, "status": "assigned", "assigned_at": timezone.now()})
        audit_change(actor=request.user, action="delivery.assigned", target=delivery, before=before, after=snapshot(delivery, ["transporter", "pickup_location", "delivery_location", "status"]), reason=str(request.data.get("reason", "Delivery assigned.")), extra={"order_id": order.id})
        return Response({"id": delivery.id, "status": delivery.status}, status=201 if created else 200)

class ProviderStatusView(APIView):
    permission_classes = [IsAdmin]
    def get(self, request): return Response([{"name": item.name, "configured": item.configured} for item in provider_statuses()])

class AuditLogList(APIView):
    permission_classes = [IsAdmin]
    def get(self, request):
        rows = AuditLog.objects.select_related("actor").order_by("-created_at")[:200]
        return Response([{"id": row.id, "actor": row.actor.username, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "metadata": row.metadata, "created_at": row.created_at} for row in rows])

class DisputeCreate(APIView):
    def post(self, request, order_id):
        order = generics.get_object_or_404(Order, id=order_id, buyer=request.user)
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10: return Response({"detail": "Please provide at least 10 characters."}, status=400)
        evidence = request.data.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 10: return Response({"detail": "Evidence must be a list with no more than 10 entries."}, status=400)
        dispute = Dispute.objects.create(order=order, opened_by=request.user, reason=reason, evidence=evidence)
        if order.status in ["delivered", "partially_fulfilled"]: transition_order(order.id, request.user, "disputed", reason, {"dispute_id": dispute.id})
        return Response({"id": dispute.id, "status": dispute.status}, status=201)

class NotificationPreferencesView(APIView):
    defaults = {"emailOrders": True, "emailMessages": True, "emailAdvisory": True, "smsOrders": False, "smsMessages": False, "smsAdvisory": True, "pushOrders": True, "pushMessages": True, "pushAdvisory": True, "pushAuctions": True}
    def get(self, request):
        pref, _ = NotificationPreference.objects.get_or_create(user=request.user)
        return Response({**self.defaults, **pref.settings})
    def put(self, request):
        allowed = {key: bool(value) for key, value in request.data.items() if key in self.defaults or "PriceAlerts" in key}
        pref, _ = NotificationPreference.objects.update_or_create(user=request.user, defaults={"settings": allowed})
        return Response(pref.settings)

class ConversationListCreate(generics.ListCreateAPIView):
    serializer_class = ConversationSerializer
    pagination_class = StandardPagination
    def get_queryset(self): return self.request.user.conversations.prefetch_related("participants", "messages__read_by").order_by("-updated_at")
    def create(self, request, *args, **kwargs):
        participant_id = request.data.get("participant_id")
        other = generics.get_object_or_404(User, id=participant_id, is_active=True)
        if other == request.user: return Response({"detail": "You cannot message yourself."}, status=400)
        conversation = Conversation.objects.filter(participants=request.user).filter(participants=other).first()
        if not conversation:
            conversation = Conversation.objects.create(); conversation.participants.add(request.user, other)
        return Response(self.get_serializer(conversation).data, status=201)

class ConversationMessages(generics.ListCreateAPIView):
    serializer_class = MessageSerializer
    pagination_class = StandardPagination
    def conversation(self): return generics.get_object_or_404(Conversation.objects.filter(participants=self.request.user), id=self.kwargs["conversation_id"])
    def get_queryset(self): return self.conversation().messages.select_related("sender").prefetch_related("read_by").order_by("created_at")
    def perform_create(self, serializer):
        conversation = self.conversation(); serializer.save(sender=self.request.user, conversation=conversation); conversation.save(update_fields=["updated_at"])

class ConversationRead(APIView):
    def post(self, request, conversation_id):
        conversation = generics.get_object_or_404(Conversation.objects.filter(participants=request.user), id=conversation_id)
        for message in conversation.messages.exclude(sender=request.user).exclude(read_by=request.user): message.read_by.add(request.user)
        return Response(status=204)

class NotificationList(generics.ListAPIView):
    serializer_class = NotificationSerializer
    pagination_class = StandardPagination
    def get_queryset(self): return request_user_notifications(self.request.user)

def request_user_notifications(user): return Notification.objects.filter(user=user).order_by("-created_at")

class NotificationRead(APIView):
    def post(self, request, notification_id):
        notification = generics.get_object_or_404(Notification, id=notification_id, user=request.user)
        if not notification.read_at: notification.read_at = timezone.now(); notification.save(update_fields=["read_at"])
        return Response(status=204)

class NotificationReadAll(APIView):
    def post(self, request):
        request_user_notifications(request.user).filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response(status=204)

PLAN_FEATURES = {"free": ["crop_planning"], "farmer-plus": ["crop_planning", "advisory_history"], "buyer-pro": ["bulk_procurement"], "cooperative": ["crop_planning", "advisory_history", "member_management"], "organization": ["crop_planning", "advisory_history", "institutional_reports"], "enterprise": ["crop_planning", "advisory_history", "institutional_reports", "data_exports"]}
PLAN_CREDITS = {"farmer-plus": 1, "cooperative": 3, "organization": 5, "enterprise": 10}

class SubscriptionMe(APIView):
    def get(self, request):
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        return Response(SubscriptionSerializer(subscription).data)

class SubscriptionCheckout(APIView):
    def post(self, request):
        plan = request.data.get("plan_id"); cycle = request.data.get("billing_cycle", "monthly"); method = request.data.get("payment_method")
        if plan not in PLAN_FEATURES or plan == "free": return Response({"detail": "Select a paid plan."}, status=400)
        allowed = ["airtel_money", "tnm_mpamba", "card"] if request.user.account_type == "individual" else ["airtel_money", "tnm_mpamba", "card", "bank_transfer", "invoice"]
        if method not in allowed or cycle not in ["monthly", "annual"]: return Response({"detail": "Invalid billing selection."}, status=400)
        if not settings.PAYMENTS_ENABLED: return Response({"detail": "Payments are not enabled."}, status=503)
        subscription, _ = Subscription.objects.update_or_create(user=request.user, defaults={"plan_id": plan, "billing_cycle": cycle, "status": "pending_payment", "enabled_features": []})
        return Response({"payment_reference": f"SUB-{subscription.id}-{int(timezone.now().timestamp())}", "status": subscription.status}, status=201)

class SubscriptionCancel(APIView):
    def post(self, request):
        subscription, _ = Subscription.objects.get_or_create(user=request.user); subscription.status = "cancelled"; subscription.save(update_fields=["status"])
        return Response(SubscriptionSerializer(subscription).data)

class CropPlanning(APIView):
    def post(self, request):
        if request.data.get("task") not in [None, "crop_recommendation", "crop_planning"]:
            return Response({"detail": "This endpoint currently supports crop planning only."}, status=400)
        try:
            result = build_crop_plan(
                location=request.data.get("location", request.user.location or "Lilongwe"),
                soil_type=request.data.get("soilType"), season=request.data.get("season"),
                preferred_crop=request.data.get("preferredCrop", ""),
            )
        except ValueError as error:
            return Response({"detail": str(error)}, status=400)
        return Response(result)

class ExpertConsultationCreate(APIView):
    def create(self, request, *args, **kwargs):
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        credits = PLAN_CREDITS.get(subscription.plan_id, 0)
        if subscription.status != "active" or not credits: return Response({"detail": "Your active plan does not include expert consultations."}, status=403)
        period = timezone.now().strftime("%Y-%m"); usage, _ = AdvisoryUsage.objects.get_or_create(user=request.user, defaults={"period": period})
        if usage.period != period: usage.period, usage.ai_requests, usage.expert_credits_used = period, 0, 0
        if usage.expert_credits_used >= credits: return Response({"detail": "Monthly expert credits have been used."}, status=429)
        try:
            expert_id = int(request.data.get("expert_id"))
            starts_at = timezone.datetime.fromisoformat(str(request.data.get("starts_at", "")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return Response({"detail": "A valid expert and consultation time are required."}, status=400)
        if expert_id <= 0 or timezone.is_naive(starts_at) or starts_at <= timezone.now():
            return Response({"detail": "Choose a valid expert and a future consultation time with a timezone."}, status=400)
        consultation = ExpertConsultation.objects.create(user=request.user, expert_id=expert_id, starts_at=starts_at)
        usage.expert_credits_used += 1; usage.save()
        return Response({"id": consultation.id, "status": consultation.status, "starts_at": consultation.starts_at}, status=201)

class DeleteAccountView(APIView):
    def post(self, request):
        password = str(request.data.get("password", ""))
        if not request.user.check_password(password): return Response({"detail": "Incorrect password."}, status=400)
        AccountDeletionRequest.objects.filter(user=request.user, used=False).update(used=True)
        code = f"{secrets.randbelow(1_000_000):06d}"
        deletion = AccountDeletionRequest.objects.create(user=request.user, expires_at=timezone.now() + timedelta(minutes=10))
        deletion.set_code(code); deletion.save(update_fields=["code_hash"])
        deliver_security_code(request.user, "Confirm MlimiConnect account deletion", f"Your account deletion code is {code}. It expires in 10 minutes. If you did not request this, change your password immediately.", f"MlimiConnect account deletion code: {code}. It expires in 10 minutes. Never share it.", "security")
        return Response({"token": str(deletion.token), "destination": "email", "masked_email": self.mask_email(request.user.email)})
    def delete(self, request):
        try: deletion = AccountDeletionRequest.objects.filter(token=request.data.get("token"), user=request.user, used=False, expires_at__gt=timezone.now()).first()
        except (DjangoValidationError, TypeError, ValueError): deletion = None
        if not deletion or not deletion.verify_code(str(request.data.get("otp", ""))): return Response({"detail": "Invalid or expired deletion code."}, status=400)
        deletion.used = True; deletion.save(update_fields=["used"])
        request.user.is_active = False
        request.user.save(update_fields=["is_active"])
        logout(request)
        return Response(status=204)
    @staticmethod
    def mask_email(email):
        name, _, domain = email.partition("@")
        return f"{name[:2]}***@{domain}"

class ReferralValidate(APIView):
    def post(self, request):
        return Response({"eligible": False, "detail": "No active referral campaign."})

class OrganizationProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = OrganizationSerializer
    def get_object(self):
        if self.request.user.account_type == "individual": raise PermissionDenied("This is not an organization account.")
        return self.request.user.organization

class OrganizationTeamView(APIView):
    def get(self, request):
        organization, _ = require_organization_permission(request.user)
        rows = OrganizationMember.objects.filter(organization=organization).select_related("user", "invited_by")
        return Response([{"id": row.id, "user_id": row.user_id, "username": row.user.username, "email": row.user.email, "role": row.role, "status": row.status, "can_procure": row.can_procure, "can_manage_members": row.can_manage_members, "can_manage_listings": row.can_manage_listings, "can_approve": row.can_approve} for row in rows])
    @transaction.atomic
    def post(self, request):
        organization, _ = require_organization_permission(request.user, "can_manage_members")
        user = generics.get_object_or_404(User, email__iexact=str(request.data.get("email", "")).strip())
        role = request.data.get("role", "member")
        if role not in dict(OrganizationMember.ROLES): return Response({"detail": "Invalid organisation role."}, status=400)
        defaults = {"role": role, "status": "active", "can_procure": bool(request.data.get("can_procure", role in ["owner", "manager", "procurement"])), "can_manage_members": bool(request.data.get("can_manage_members", role in ["owner", "manager"])), "can_manage_listings": bool(request.data.get("can_manage_listings", role in ["owner", "manager", "seller"])), "can_approve": bool(request.data.get("can_approve", role in ["owner", "manager"])), "invited_by": request.user}
        member, created = OrganizationMember.objects.update_or_create(organization=organization, user=user, defaults=defaults)
        audit_change(actor=request.user, action="organization.member_added" if created else "organization.member_updated", target=member, before={}, after=snapshot(member, ["user", "role", "status", "can_procure", "can_manage_members", "can_manage_listings", "can_approve"]), reason=str(request.data.get("reason", "Team membership updated.")), extra={"organization_id": organization.id})
        return Response({"id": member.id, "status": member.status}, status=201 if created else 200)
    @transaction.atomic
    def patch(self, request):
        organization, _ = require_organization_permission(request.user, "can_manage_members")
        member = generics.get_object_or_404(OrganizationMember.objects.select_for_update(), id=request.data.get("member_id"), organization=organization)
        if member.user_id == organization.owner_id: return Response({"detail": "The organisation owner cannot be suspended or delegated here."}, status=400)
        before = snapshot(member, ["role", "status", "can_procure", "can_manage_members", "can_manage_listings", "can_approve"])
        for field in ["role", "status", "can_procure", "can_manage_members", "can_manage_listings", "can_approve"]:
            if field in request.data: setattr(member, field, request.data[field])
        if member.role not in dict(OrganizationMember.ROLES) or member.status not in dict(OrganizationMember.STATUSES): return Response({"detail": "Invalid role or status."}, status=400)
        member.save()
        audit_change(actor=request.user, action="organization.member_updated", target=member, before=before, after=snapshot(member, before.keys()), reason=str(request.data.get("reason", "Team membership updated.")), extra={"organization_id": organization.id})
        return Response({"id": member.id, "status": member.status, "role": member.role})

class OrganizationApprovalsView(APIView):
    def get(self, request):
        organization, _ = require_organization_permission(request.user)
        rows = TeamApprovalRequest.objects.filter(organization=organization).select_related("requested_by", "reviewed_by").order_by("-created_at")
        return Response([{"id": row.id, "action_type": row.action_type, "payload": row.payload, "status": row.status, "requested_by": row.requested_by.username, "reviewed_by": row.reviewed_by.username if row.reviewed_by else None, "review_reason": row.review_reason, "created_at": row.created_at} for row in rows])
    def post(self, request):
        organization, membership = require_organization_permission(request.user)
        action_type = str(request.data.get("action_type", "")).strip()
        if not action_type: return Response({"detail": "Action type is required."}, status=400)
        item = TeamApprovalRequest.objects.create(organization=organization, action_type=action_type[:50], payload=request.data.get("payload", {}), requested_by=request.user)
        audit_change(actor=request.user, action="organization.approval_requested", target=item, before={}, after=snapshot(item, ["action_type", "payload", "status"]), reason=str(request.data.get("reason", "Team approval requested.")), extra={"organization_id": organization.id})
        return Response({"id": item.id, "status": item.status}, status=201)
    @transaction.atomic
    def patch(self, request):
        organization, _ = require_organization_permission(request.user, "can_approve")
        item = generics.get_object_or_404(TeamApprovalRequest.objects.select_for_update(), id=request.data.get("approval_id"), organization=organization, status="pending")
        decision, reason = request.data.get("decision"), str(request.data.get("reason", "")).strip()
        if decision not in ["approved", "rejected"] or len(reason) < 5: return Response({"detail": "Decision and reason are required."}, status=400)
        if item.requested_by_id == request.user.id: return Response({"detail": "A requester cannot approve their own action."}, status=400)
        before = snapshot(item, ["status", "reviewed_by", "review_reason"]); item.status, item.reviewed_by, item.review_reason, item.reviewed_at = decision, request.user, reason, timezone.now(); item.save(update_fields=["status", "reviewed_by", "review_reason", "reviewed_at"])
        audit_change(actor=request.user, action=f"organization.approval_{decision}", target=item, before=before, after=snapshot(item, ["status", "reviewed_by", "review_reason"]), reason=reason, extra={"organization_id": organization.id})
        return Response({"id": item.id, "status": item.status})

class OrganizationDocumentsView(APIView):
    def get(self, request):
        organization, _ = require_organization_permission(request.user)
        return Response([{"id": row.id, "document_type": row.document_type, "file": row.file.url, "created_at": row.created_at} for row in organization.documents.order_by("-created_at")])
    def post(self, request):
        organization, _ = require_organization_permission(request.user, "can_manage_members")
        upload = request.FILES.get("file")
        if not upload or upload.size > 5 * 1024 * 1024 or upload.content_type not in ["image/jpeg", "image/png", "application/pdf"]: return Response({"detail": "Upload a JPG, PNG or PDF no larger than 5 MB."}, status=400)
        row = OrganizationDocument.objects.create(organization=organization, uploaded_by=request.user, document_type=str(request.data.get("document_type", "other"))[:50], file=upload)
        audit_change(actor=request.user, action="organization.document_uploaded", target=row, before={}, after={"document_type": row.document_type, "file_name": upload.name}, reason="Organisation document uploaded.", extra={"organization_id": organization.id})
        return Response({"id": row.id}, status=201)

class OrganizationReportView(APIView):
    def get(self, request):
        organization, _ = require_organization_permission(request.user)
        listings = organization.shared_listings.all(); order_items = __import__("core.models", fromlist=["OrderItem"]).OrderItem.objects.filter(listing__organization=organization)
        return Response({"organization": organization.legal_name, "team": {"active": organization.team_members.filter(status="active").count(), "total": organization.team_members.count()}, "listings": {"total": listings.count(), "approved": listings.filter(approval_status="approved").count(), "stock_units": listings.aggregate(value=Sum("quantity"))["value"] or 0}, "sales": {"orders": order_items.values("order_id").distinct().count(), "gross_value": order_items.aggregate(value=Sum("order__total"))["value"] or 0}, "procurement": {"orders": organization.procurement_orders.count(), "gross_value": organization.procurement_orders.aggregate(value=Sum("total"))["value"] or 0}, "orders": {"count": order_items.values("order_id").distinct().count(), "gross_value": order_items.aggregate(value=Sum("order__total"))["value"] or 0}, "approvals_pending": organization.approval_requests.filter(status="pending").count()})

class OrganizationAuditView(APIView):
    def get(self, request):
        organization, _ = require_organization_permission(request.user)
        rows = AuditLog.objects.filter(metadata__organization_id=organization.id).select_related("actor").order_by("-created_at")[:200]
        return Response([{"id": row.id, "actor": row.actor.username, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "metadata": row.metadata, "created_at": row.created_at} for row in rows])

@method_decorator(csrf_exempt, name="dispatch")
class USSDAuthenticateView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [USSDRateThrottle]
    def post(self, request):
        if not ussd_source_allowed(request):return Response({"detail":"Source IP is not allowed."},status=403)
        configured_key = settings.USSD_SERVICE_KEY
        supplied_key = request.headers.get("X-USSD-Service-Key", "")
        if not configured_key or not supplied_key or not __import__("secrets").compare_digest(configured_key, supplied_key):
            return Response({"detail": "Unauthorized service."}, status=403)
        phone = str(request.data.get("phone", "")).replace(" ", "").replace("-", "")
        pin = str(request.data.get("pin", ""))
        if not phone.startswith("+265") or not pin.isdigit() or len(pin) != 4:
            return Response({"authenticated": False})
        credentials = list(USSDCredential.objects.select_related("user").filter(user__phone=phone, user__is_active=True, enabled=True)[:2])
        credential = credentials[0] if len(credentials) == 1 else None
        authenticated = bool(credential and credential.verify(pin)); OperationalEvent.objects.create(category="ussd", name="authentication", status="success" if authenticated else "denied", correlation_id=getattr(request, "correlation_id", "unknown"), metadata={})
        return Response({"authenticated": authenticated})

class USSDServiceView(APIView):
    permission_classes=[permissions.AllowAny];authentication_classes=[];throttle_classes=[USSDRateThrottle]
    def get(self,request,service):
        if not settings.USSD_SERVICE_KEY or not hmac.compare_digest(request.headers.get("X-USSD-Service-Key",""),settings.USSD_SERVICE_KEY): return Response({"detail":"Forbidden."},status=403)
        if not ussd_source_allowed(request):return Response({"detail":"Source IP is not allowed."},status=403)
        key=f"ussd-service:{service}:{hashlib.sha256(json.dumps(dict(request.query_params),sort_keys=True).encode()).hexdigest()[:20]}";cached=cache.get(key)
        if cached:return Response({**cached,"cached":True})
        if service=="prices":
            rows=Listing.objects.filter(is_active=True,approval_status="approved").values("category","unit").annotate(price=__import__("django.db.models",fromlist=["Avg"]).Avg("price"),count=Count("id")).order_by("category")[:5];data={"items":[{"crop":r["category"],"unit":r["unit"],"price":round(r["price"]),"count":r["count"]}for r in rows],"source":"MlimiConnect approved listings","collected_at":timezone.now()}
        elif service=="orders":
            phone=str(request.query_params.get("phone","")).strip();user=User.objects.filter(phone=phone).first();rows=Order.objects.filter(buyer=user).order_by("-created_at")[:3]if user else[];data={"items":[{"id":r.id,"status":r.status,"total":r.total}for r in rows]}
        elif service=="weather":
            district=str(request.query_params.get("district","")).strip()[:80]
            try:
                weather=get_weather(district or "Lilongwe");current=weather["current"];today=weather["forecast"][0] if weather["forecast"] else {}
                data={"district":weather["location"],"summary":f'{current["condition"]}, {current["temperature_c"]} C. Rain chance {today.get("rain_probability_percent") or 0}%.',"source":weather["source"],"collected_at":weather["collected_at"],"stale":weather["stale"]}
            except ValueError as error:return Response({"detail":str(error)},status=400)
            except WeatherUnavailable:return Response({"detail":"Weather service is temporarily unavailable."},status=503)
        elif service=="pest":
            crop=str(request.query_params.get("crop","")).strip()[:50];tips={"maize":"Inspect the whorl for fall armyworm. Use locally approved controls and contact an extension officer.","tomato":"Inspect leaf undersides, remove badly affected material, and ask an extension officer before pesticide use."};data={"crop":crop,"guidance":tips.get(crop.lower(),"Isolate affected plants, avoid handling healthy plants afterward, and contact an agricultural extension officer."),"source":"MlimiConnect safety knowledge base","diagnosis":False}
        elif service=="support":data={"phone":settings.SUPPORT_PHONE,"email":settings.SUPPORT_EMAIL,"hours":"Mon-Sat 07:00-18:00 CAT"}
        else:return Response({"detail":"Unknown USSD service."},status=404)
        cache.set(key,data,300 if service in{"orders","weather"}else 1800);return Response({**data,"cached":False})


class WeatherAdvisoryView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        try:
            data = get_weather(
                request.query_params.get("district", "Lilongwe"),
                request.query_params.get("latitude"),
                request.query_params.get("longitude"),
            )
            return Response(data)
        except ValueError as error:
            return Response({"detail": str(error)}, status=400)
        except WeatherUnavailable as error:
            return Response({"detail": str(error)}, status=503)

class USSDCredentialView(APIView):
    def post(self,request):
        pin=str(request.data.get("pin",""));ack=request.data.get("privacy_ack")is True
        if not ack:return Response({"detail":"Confirm that the PIN is stored securely and must never be shared."},status=400)
        try:credential,_=USSDCredential.objects.get_or_create(user=request.user);credential.set_pin(pin);credential.enabled=True;credential.save()
        except ValueError as error:return Response({"detail":str(error)},status=400)
        AuditLog.objects.create(actor=request.user,action="ussd.pin_created",target_type="user",target_id=str(request.user.id),metadata={"privacy_ack":True});return Response({"enabled":True,"privacy_notice":"Never share this PIN. MlimiConnect will never ask for a mobile-money PIN."})
    def delete(self,request):USSDCredential.objects.filter(user=request.user).update(enabled=False);return Response(status=204)

class USSDRecoveryView(APIView):
    permission_classes=[permissions.AllowAny];authentication_classes=[]
    def post(self,request):
        phone=str(request.data.get("phone","")).strip();user=User.objects.filter(phone=phone).first()
        if user:AuditLog.objects.create(actor=user,action="ussd.recovery_requested",target_type="user",target_id=str(user.id),metadata={"support_led":True})
        return Response({"detail":"If the account exists, support will contact the registered account owner.","support_phone":settings.SUPPORT_PHONE})

class AdminUSSDRecoveryView(APIView):
    permission_classes=[IsAdmin]
    def post(self,request,user_id):
        user=generics.get_object_or_404(User,id=user_id);action=request.data.get("action");reason=str(request.data.get("reason","")).strip()
        if action not in{"disable","issue_temporary_pin"}or len(reason)<10:return Response({"detail":"Choose a recovery action and provide a reason of at least 10 characters."},status=400)
        credential,_=USSDCredential.objects.get_or_create(user=user)
        if action=="disable":credential.enabled=False;temporary=None
        else:
            temporary=f"{secrets.randbelow(10000):04d}";credential.set_pin(temporary);credential.enabled=True
        credential.save();audit_change(actor=request.user,action=f"ussd.recovery_{action}",target=user,before={},after={"enabled":credential.enabled},reason=reason)
        return Response({"enabled":credential.enabled,"temporary_pin":temporary,"delivery":"Support must deliver this once after identity verification."})

class ForgotPasswordView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        method = request.data.get("method")
        if method not in {"email", "phone"}: return Response({"detail": "Choose email or phone recovery."}, status=400)
        if method == "email":
            identifier = str(request.data.get("email", "")).strip().lower()
            user = User.objects.filter(email__iexact=identifier, is_active=True).first()
        else:
            identifier = str(request.data.get("phone", "")).replace(" ", "").replace("-", "")
            identifier = identifier if identifier.startswith("+265") else f"+265{identifier[1:]}" if identifier.startswith("0") else identifier
            user = User.objects.filter(phone=identifier, is_active=True).first()
        if user:
            code = f"{secrets.randbelow(1_000_000):06d}"
            reset = PasswordResetRequest.objects.create(user=user, expires_at=timezone.now() + timedelta(minutes=10))
            reset.set_code(code); reset.save(update_fields=["code_hash"])
            link = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={reset.token}"
            deliver_security_code(user, "MlimiConnect password reset", f"Your reset code is {code}. Open {link}. It expires in 10 minutes.", f"MlimiConnect password reset code: {code}. It expires in 10 minutes. Never share this code.", "security")
        return Response({"message": "If that account exists, a reset code has been sent."})

def valid_reset(data):
    try: reset = PasswordResetRequest.objects.select_related("user").filter(token=data.get("token"), used=False, expires_at__gt=timezone.now()).first()
    except (DjangoValidationError, TypeError, ValueError): return None
    return reset if reset and reset.verify_code(str(data.get("otp", ""))) else None

class VerifyResetCodeView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        reset = valid_reset(request.data)
        if not reset: return Response({"detail": "Invalid or expired code."}, status=400)
        reset.verified = True; reset.save(update_fields=["verified"])
        return Response({"verified": True})

class ResetPasswordView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        reset = valid_reset(request.data)
        if not reset or not reset.verified: return Response({"detail": "Verify the reset code first."}, status=400)
        password = str(request.data.get("password", ""))
        try: password_validation.validate_password(password, reset.user)
        except Exception as error: return Response({"detail": " ".join(error.messages)}, status=400)
        reset.user.set_password(password); reset.user.save(update_fields=["password"])
        reset.used = True; reset.save(update_fields=["used"])
        return Response({"message": "Password updated."})

def _herd_for(user, herd_id):
    return generics.get_object_or_404(HerdFlock, id=herd_id, owner=user)

def _herd_data(row, expanded=False):
    data = {"id": row.id, "name": row.name, "species": row.species, "breed": row.breed, "purpose": row.purpose, "head_count": row.head_count, "housing": row.housing, "status": row.status, "traceability_batch_id": row.traceability_batch_id, "traceability_code": row.traceability_batch.batch_code if row.traceability_batch_id else None, "updated_at": row.updated_at}
    if expanded:
        data["animals"] = list(row.animals.values("id", "identifier", "sex", "date_of_birth", "estimated_age_months", "weight_kg", "acquisition_type", "acquisition_date", "breeding_status", "status", "notes"))
        data["health_events"] = list(row.health_events.values("id", "animal_id", "event_type", "occurred_on", "description", "product_or_vaccine", "administered_by", "withdrawal_end_date", "verification_status").order_by("-occurred_on")[:100])
        data["production_records"] = list(row.production_records.values("id", "record_type", "recorded_on", "quantity", "unit", "notes").order_by("-recorded_on")[:100])
        data["reminders"] = list(row.vaccination_reminders.values("id", "animal_id", "vaccine_name", "due_on", "notes", "status").order_by("due_on"))
        data["breeding_records"] = list(row.breeding_records.values("id", "animal_id", "event_type", "occurred_on", "expected_due_date", "outcome", "notes").order_by("-occurred_on")[:100])
        data["financial_records"] = list(row.financial_records.values("id", "record_type", "category", "amount", "occurred_on", "description").order_by("-occurred_on")[:100])
        data["alerts"] = list(row.alerts.filter(resolved_at=None).values("id", "alert_type", "level", "message", "created_at"))
        totals = row.financial_records.values("record_type").annotate(total=Sum("amount")); summary = {item["record_type"]: item["total"] for item in totals}
        data["profitability"] = {"income": summary.get("income", Decimal("0")), "expenses": summary.get("expense", Decimal("0")), "net": summary.get("income", Decimal("0")) - summary.get("expense", Decimal("0"))}
    return data

class LivestockProfileView(APIView):
    def get(self, request):
        row = LivestockProfile.objects.filter(user=request.user).first()
        return Response(None if not row else {"farm_name": row.farm_name, "production_system": row.production_system, "species_kept": row.species_kept, "district": row.district, "traditional_authority": row.traditional_authority, "biosecurity_notes": row.biosecurity_notes, "veterinary_contact": row.veterinary_contact, "verified": row.verified})
    def put(self, request):
        name, system, species = str(request.data.get("farm_name", "")).strip(), str(request.data.get("production_system", "mixed")), request.data.get("species_kept", [])
        if not name or system not in dict(LivestockProfile.SYSTEMS) or not isinstance(species, list): return Response({"detail": "Farm name, a valid production system, and species list are required."}, status=400)
        _, created = LivestockProfile.objects.update_or_create(user=request.user, defaults={"farm_name": name[:160], "production_system": system, "species_kept": species, "district": str(request.data.get("district", ""))[:100], "traditional_authority": str(request.data.get("traditional_authority", ""))[:100], "biosecurity_notes": str(request.data.get("biosecurity_notes", "")), "veterinary_contact": str(request.data.get("veterinary_contact", ""))[:140]})
        if created and not request.user.can_sell:
            request.user.can_sell = True; request.user.user_type = "farmer"; request.user.save(update_fields=["can_sell", "user_type"])
        return self.get(request)

class HerdFlockListCreate(APIView):
    def get(self, request): return Response([_herd_data(row) for row in HerdFlock.objects.filter(owner=request.user).order_by("-updated_at")])
    def post(self, request):
        name, species = str(request.data.get("name", "")).strip(), str(request.data.get("species", ""))
        try: count = int(request.data.get("head_count", 0))
        except (TypeError, ValueError): return Response({"detail": "Head count must be a whole number."}, status=400)
        if not name or species not in dict(HerdFlock.SPECIES) or count < 0: return Response({"detail": "Name, valid species, and non-negative head count are required."}, status=400)
        organization, _ = organization_access(request.user)
        batch = TraceabilityBatch.objects.create(owner=request.user, batch_code=f"LIV-{request.user.id}-{secrets.token_hex(5).upper()}", product=f"{species}: {name[:140]}", quantity=f"{count} head", public_data={"domain": "livestock", "species": species})
        row = HerdFlock.objects.create(owner=request.user, organization=organization, traceability_batch=batch, name=name[:140], species=species, breed=str(request.data.get("breed", ""))[:100], purpose=str(request.data.get("purpose", ""))[:100], head_count=count, housing=str(request.data.get("housing", ""))[:180])
        append_event(batch_id=batch.id, actor=request.user, event_type="acquisition", stage="acquisition", description="Herd or flock record established.", location=request.user.location, quantity=count, unit="head")
        Notification.objects.create(user=request.user, type="livestock", title="Herd or flock added", message=f"{row.name} is ready for animal, health, and production records.", action_url="/app/livestock")
        return Response(_herd_data(row), status=201)

class HerdFlockDetail(APIView):
    def get(self, request, herd_id): return Response(_herd_data(_herd_for(request.user, herd_id), True))
    def patch(self, request, herd_id):
        row = _herd_for(request.user, herd_id)
        for field in ["name", "breed", "purpose", "housing", "status"]:
            if field in request.data: setattr(row, field, request.data[field])
        if "head_count" in request.data: row.head_count = max(0, int(request.data["head_count"]))
        row.save(); return Response(_herd_data(row, True))

class HerdAnimalsView(APIView):
    def post(self, request, herd_id):
        herd, identifier = _herd_for(request.user, herd_id), str(request.data.get("identifier", "")).strip()
        if not identifier: return Response({"detail": "An ear tag, ring, batch, or other identifier is required."}, status=400)
        row = AnimalRecord.objects.create(herd=herd, identifier=identifier[:100], sex=request.data.get("sex", "unknown"), date_of_birth=request.data.get("date_of_birth") or None, estimated_age_months=request.data.get("estimated_age_months") or None, weight_kg=request.data.get("weight_kg") or None, acquisition_type=request.data.get("acquisition_type", "unknown"), acquisition_date=request.data.get("acquisition_date") or None, breeding_status=request.data.get("breeding_status", "not_recorded"), notes=str(request.data.get("notes", "")))
        if herd.traceability_batch_id: append_event(batch_id=herd.traceability_batch_id, actor=request.user, event_type=row.acquisition_type if row.acquisition_type != "unknown" else "acquisition", stage="acquisition", description=f"Animal {row.identifier} added.", location=request.user.location, quantity=1, unit="head")
        return Response({"id": row.id, "identifier": row.identifier}, status=201)

class HerdHealthEventsView(APIView):
    def post(self, request, herd_id):
        herd, kind = _herd_for(request.user, herd_id), request.data.get("event_type")
        if kind not in dict(LivestockHealthEvent.TYPES) or not request.data.get("occurred_on") or not str(request.data.get("description", "")).strip(): return Response({"detail": "Event type, date, and description are required."}, status=400)
        animal = herd.animals.filter(id=request.data.get("animal_id")).first() if request.data.get("animal_id") else None
        row = LivestockHealthEvent.objects.create(herd=herd, animal=animal, event_type=kind, occurred_on=request.data["occurred_on"], description=request.data["description"], product_or_vaccine=str(request.data.get("product_or_vaccine", ""))[:140], administered_by=str(request.data.get("administered_by", ""))[:140], withdrawal_end_date=request.data.get("withdrawal_end_date") or None, evidence=request.FILES.get("evidence"), created_by=request.user)
        if herd.traceability_batch_id and kind in ["vaccination", "inspection", "birth", "death"]: append_event(batch_id=herd.traceability_batch_id, actor=request.user, event_type=kind, stage=kind, description=row.description, location=request.user.location, quantity=1 if animal else herd.head_count, unit="head", uploads=[row.evidence.file] if row.evidence else [])
        if kind == "death":
            LivestockAlert.objects.create(herd=herd, alert_type="mortality", level="critical", message=f"A mortality event was recorded for {herd.name}. Review the cause and contact a livestock professional if deaths are unusual or increasing.")
            Notification.objects.create(user=request.user, type="livestock_alert", title="Mortality record needs review", message=f"A death was recorded for {herd.name}. Monitor the remaining animals and escalate unusual mortality.", action_url="/app/livestock")
        return Response({"id": row.id, "verification_status": row.verification_status}, status=201)

class HerdProductionRecordsView(APIView):
    def post(self, request, herd_id):
        herd, kind = _herd_for(request.user, herd_id), request.data.get("record_type")
        if kind not in dict(LivestockProductionRecord.TYPES): return Response({"detail": "Select a valid production record type."}, status=400)
        try: quantity = Decimal(str(request.data.get("quantity")))
        except (InvalidOperation, TypeError): return Response({"detail": "Enter a valid quantity."}, status=400)
        if quantity < 0 or not request.data.get("recorded_on") or not request.data.get("unit"): return Response({"detail": "Date, unit, and a non-negative quantity are required."}, status=400)
        row = LivestockProductionRecord.objects.create(herd=herd, record_type=kind, recorded_on=request.data["recorded_on"], quantity=quantity, unit=str(request.data["unit"])[:20], notes=str(request.data.get("notes", "")), created_by=request.user)
        previous = herd.production_records.filter(record_type=kind).exclude(id=row.id).order_by("-recorded_on").first()
        if previous and previous.quantity > 0 and quantity < previous.quantity * Decimal("0.70"):
            LivestockAlert.objects.create(herd=herd, alert_type="production_drop", level="warning", message=f"{kind.replace('_', ' ').title()} fell by more than 30% compared with the previous record.")
            Notification.objects.create(user=request.user, type="livestock_alert", title="Production drop detected", message=f"Review {herd.name}: {kind.replace('_', ' ')} has fallen sharply.", action_url="/app/livestock")
        return Response({"id": row.id}, status=201)

class VaccinationRemindersView(APIView):
    def get(self, request):
        rows = VaccinationReminder.objects.filter(herd__owner=request.user).select_related("herd").order_by("due_on")
        return Response([{"id": x.id, "herd_id": x.herd_id, "herd_name": x.herd.name, "animal_id": x.animal_id, "vaccine_name": x.vaccine_name, "due_on": x.due_on, "notes": x.notes, "status": x.status} for x in rows])
    def post(self, request):
        herd = _herd_for(request.user, request.data.get("herd_id"))
        if not request.data.get("vaccine_name") or not request.data.get("due_on"): return Response({"detail": "Vaccine name and due date are required."}, status=400)
        animal = herd.animals.filter(id=request.data.get("animal_id")).first() if request.data.get("animal_id") else None
        row = VaccinationReminder.objects.create(herd=herd, animal=animal, vaccine_name=str(request.data["vaccine_name"])[:140], due_on=request.data["due_on"], notes=str(request.data.get("notes", "")), created_by=request.user)
        Notification.objects.create(user=request.user, type="livestock_reminder", title="Vaccination reminder scheduled", message=f"{row.vaccine_name} is due on {row.due_on} for {herd.name}.", action_url="/app/livestock")
        return Response({"id": row.id, "status": row.status}, status=201)
    def patch(self, request):
        row = generics.get_object_or_404(VaccinationReminder, id=request.data.get("id"), herd__owner=request.user)
        if request.data.get("status") not in dict(VaccinationReminder.STATUSES): return Response({"detail": "Invalid reminder status."}, status=400)
        row.status = request.data["status"]; row.save(update_fields=["status"]); return Response({"id": row.id, "status": row.status})

class LivestockAdvisoryView(APIView):
    def get(self, request):
        species = str(request.query_params.get("species", "general"))
        common = ["Provide species-appropriate housing with clean water, safe feed, shade and ventilation.", "Use clean equipment, limit unnecessary visitors, quarantine new animals, and isolate visibly sick animals.", "Ask a qualified livestock professional for a locally appropriate vaccination and parasite-prevention schedule."]
        specific = {"chickens_broilers": ["Keep brooding temperature stable for young chicks and reduce it gradually as they feather.", "Keep litter dry, prevent overcrowding, and monitor feed, water and daily mortality."], "chickens_layers": ["Provide appropriate layer feed and calcium access, secure nesting areas, and record sudden egg-production drops."], "chickens_indigenous": ["Protect young birds from cold, predators and contaminated drinking water; use locally suitable biosecurity."], "cattle": ["Watch for udder heat, swelling, pain, abnormal milk or a sudden milk drop as mastitis warning signs.", "Respect recorded medicine withdrawal periods before selling milk or meat."], "goats": ["Provide dry shelter and monitor body condition, appetite and signs of parasite burden."], "pigs": ["Maintain strong entry biosecurity, avoid unsafe feed sources, and urgently report clusters of fever or sudden death."], "ducks": ["Keep drinking water clean, sleeping areas dry, and separate domestic birds from wild-bird contact where practical."]}
        weather, heat_alert = None, None
        try:
            weather = get_weather(request.query_params.get("district") or getattr(request.user, "location", "") or "Lilongwe", request.query_params.get("latitude"), request.query_params.get("longitude"))
            current_temp = weather.get("current", {}).get("temperature_c"); forecast_max = max([x.get("temperature_max_c") or -99 for x in weather.get("forecast", [])], default=-99)
            if (current_temp is not None and current_temp >= 30) or forecast_max >= 32: heat_alert = "Heat-stress risk: increase clean water access, shade and airflow; avoid stressful handling during the hottest hours and monitor breathing closely."
        except (WeatherUnavailable, ValueError): pass
        today = timezone.localdate(); restrictions = LivestockMovementRestriction.objects.filter(active=True, starts_on__lte=today).filter(Q(ends_on=None)|Q(ends_on__gte=today)).filter(Q(species="")|Q(species=species))
        return Response({"species": species, "guidance": common + specific.get(species, []), "heat_stress_alert": heat_alert, "weather": weather, "outbreak_and_movement_warnings": list(restrictions.values("reason", "origin_region", "destination_region", "starts_on", "ends_on", "source_name", "source_reference")), "source": "MlimiConnect safety-reviewed husbandry knowledge base", "source_updated": "2026-08-30", "coverage": "General husbandry only; vaccination schedules, outbreak status and treatment decisions require verified Malawi veterinary or government sources.", "stale_after_days": 180, "warning": "This is not a diagnosis and does not prescribe medicines, vaccines, dosages, or pesticides.", "isolate_when": ["unusual breathing, diarrhoea, discharge, weakness or neurological signs appear", "a contagious disease is suspected", "a new animal enters the farm before professional clearance"], "urgent_signs": ["difficulty breathing", "severe bleeding", "collapse", "rapid unexplained deaths", "suspected reportable disease"], "escalation_path": "/api/v1/advisory/expert-consultations/"})

class LiveAnimalListingDetailView(APIView):
    def get(self, request, listing_id):
        row = generics.get_object_or_404(LiveAnimalListingDetail.objects.select_related("listing"), listing_id=listing_id)
        if row.listing.seller != request.user and row.verification_status != "verified" and not request.user.is_staff: raise PermissionDenied("This live-animal record is awaiting verification.")
        return Response({"listing_id": listing_id, "herd_id": row.herd_id, "species": row.species, "breed": row.breed, "sex": row.sex, "date_of_birth": row.date_of_birth, "age_months": row.age_months, "sale_format": row.sale_format, "sale_quantity": row.sale_quantity, "live_weight_kg": row.live_weight_kg, "purpose": row.purpose, "health_inspection_date": row.health_inspection_date, "animal_identifier": row.animal_identifier, "breeding_status": row.breeding_status, "production_summary": row.production_summary, "transport_available": row.transport_available, "handling_requirements": row.handling_requirements, "vaccination_summary": row.vaccination_summary, "welfare_declaration": row.welfare_declaration, "movement_permit_reference": row.movement_permit_reference, "has_veterinary_certificate": bool(row.veterinary_certificate), "verification_status": row.verification_status, "verification_reason": row.verification_reason})
    def post(self, request, listing_id):
        listing, herd = generics.get_object_or_404(Listing, id=listing_id, seller=request.user), _herd_for(request.user, request.data.get("herd_id"))
        if request.data.get("welfare_declaration") not in [True, "true", "True", "1", 1]: return Response({"detail": "The animal-welfare declaration must be accepted."}, status=400)
        today = timezone.localdate(); district = getattr(request.user, "livestock_profile", None).district if hasattr(request.user, "livestock_profile") else request.user.location
        restricted = LivestockMovementRestriction.objects.filter(active=True, starts_on__lte=today).filter(Q(ends_on=None)|Q(ends_on__gte=today)).filter(Q(species="")|Q(species=herd.species)).filter(Q(origin_region="")|Q(origin_region__iexact=district)).first()
        if restricted: return Response({"detail": f"This listing is blocked by an active movement restriction: {restricted.reason}", "source": restricted.source_name}, status=409)
        if LivestockCatalogueEntry.objects.filter(entry_type="prohibited_sale", active=True).filter(Q(species="")|Q(species=herd.species)).exists(): return Response({"detail": "This animal sale matches an active prohibited-sale rule and cannot be published."}, status=409)
        row, _ = LiveAnimalListingDetail.objects.update_or_create(listing=listing, defaults={"herd": herd, "species": herd.species, "breed": str(request.data.get("breed", herd.breed))[:100], "sex": request.data.get("sex", "mixed"), "date_of_birth": request.data.get("date_of_birth") or None, "age_months": request.data.get("age_months") or None, "sale_format": request.data.get("sale_format", "group"), "sale_quantity": request.data.get("sale_quantity") or listing.quantity, "live_weight_kg": request.data.get("live_weight_kg") or None, "purpose": request.data.get("purpose", "other"), "health_inspection_date": request.data.get("health_inspection_date") or None, "animal_identifier": str(request.data.get("animal_identifier", ""))[:120], "breeding_status": str(request.data.get("breeding_status", ""))[:100], "production_summary": str(request.data.get("production_summary", "")), "transport_available": request.data.get("transport_available") in [True, "true", "1", 1], "handling_requirements": str(request.data.get("handling_requirements", "")), "vaccination_summary": str(request.data.get("vaccination_summary", "")), "welfare_declaration": True, "movement_permit_reference": str(request.data.get("movement_permit_reference", ""))[:120], "veterinary_certificate": request.FILES.get("veterinary_certificate"), "verification_status": "pending"})
        listing.approval_status = "pending"; listing.save(update_fields=["approval_status"])
        return Response({"id": row.id, "verification_status": row.verification_status}, status=201)

class AdminLiveAnimalVerificationView(APIView):
    permission_classes = [permissions.IsAdminUser]
    def post(self, request, listing_id):
        row, decision = generics.get_object_or_404(LiveAnimalListingDetail, listing_id=listing_id), request.data.get("decision")
        if decision not in ["verified", "rejected"]: return Response({"detail": "Decision must be verified or rejected."}, status=400)
        audit_fields = ["verification_status", "verification_reason", "verified_by", "verified_at"]
        before = snapshot(row, audit_fields); row.verification_status = decision; row.verification_reason = str(request.data.get("reason", "")); row.verified_by = request.user; row.verified_at = timezone.now(); row.save()
        listing = row.listing; listing.approval_status = "approved" if decision == "verified" else "rejected"; listing.moderation_reason = row.verification_reason; listing.moderated_by = request.user; listing.moderated_at = timezone.now(); listing.save(update_fields=["approval_status", "moderation_reason", "moderated_by", "moderated_at"])
        audit_change(actor=request.user, action="live_animal_listing.verification", target=row, before=before, after=snapshot(row, audit_fields), reason=row.verification_reason)
        Notification.objects.create(user=listing.seller, type="listing_verification", title=f"Live-animal listing {decision}", message=row.verification_reason or f"Your listing has been {decision}.", action_url="/app/livestock")
        return Response({"listing_id": listing_id, "verification_status": decision})

class LivestockDeliveryRequirementsView(APIView):
    def get(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery.objects.select_related("order", "transporter"), id=delivery_id)
        if request.user not in [delivery.order.buyer, delivery.transporter] and not request.user.is_staff: raise PermissionDenied()
        row = getattr(delivery, "livestock_requirements", None)
        return Response(None if not row else {"movement_permit_reference": row.movement_permit_reference, "transporter_authorization_reference": row.transporter_authorization_reference, "vehicle_suitable": row.vehicle_suitable, "ventilation_confirmed": row.ventilation_confirmed, "cleaning_confirmed": row.cleaning_confirmed, "disinfection_record": row.disinfection_record, "maximum_stocking_count": row.maximum_stocking_count, "stocking_plan": row.stocking_plan, "welfare_plan": row.welfare_plan, "emergency_contact": row.emergency_contact, "approved": bool(row.approved_at)})
    def put(self, request, delivery_id):
        delivery = generics.get_object_or_404(Delivery, id=delivery_id)
        if request.user != delivery.transporter and not request.user.is_staff: raise PermissionDenied("Assigned transporter or administrator required.")
        row, _ = LivestockDeliveryRequirement.objects.update_or_create(delivery=delivery, defaults={"movement_permit_reference": str(request.data.get("movement_permit_reference", ""))[:120], "transporter_authorization_reference": str(request.data.get("transporter_authorization_reference", ""))[:120], "vehicle_suitable": request.data.get("vehicle_suitable") in [True, "true", "1", 1], "ventilation_confirmed": request.data.get("ventilation_confirmed") in [True, "true", "1", 1], "cleaning_confirmed": request.data.get("cleaning_confirmed") in [True, "true", "1", 1], "disinfection_record": str(request.data.get("disinfection_record", "")), "maximum_stocking_count": request.data.get("maximum_stocking_count") or None, "stocking_plan": str(request.data.get("stocking_plan", "")), "welfare_plan": str(request.data.get("welfare_plan", "")), "emergency_contact": str(request.data.get("emergency_contact", ""))[:120]})
        if all([row.transporter_authorization_reference, row.vehicle_suitable, row.ventilation_confirmed, row.cleaning_confirmed, row.disinfection_record, row.maximum_stocking_count, row.stocking_plan, row.welfare_plan, row.emergency_contact]): row.approved_by = request.user; row.approved_at = timezone.now(); row.save(update_fields=["approved_by", "approved_at"])
        return self.get(request, delivery_id)

class AnimalWeightHistoryView(APIView):
    def get(self, request, animal_id):
        animal = generics.get_object_or_404(AnimalRecord, id=animal_id, herd__owner=request.user)
        return Response(list(animal.weight_history.values("id", "recorded_on", "weight_kg", "created_at").order_by("-recorded_on")))
    def post(self, request, animal_id):
        animal = generics.get_object_or_404(AnimalRecord, id=animal_id, herd__owner=request.user)
        try: weight = Decimal(str(request.data.get("weight_kg")))
        except (InvalidOperation, TypeError): return Response({"detail": "Enter a valid weight."}, status=400)
        if weight <= 0 or not request.data.get("recorded_on"): return Response({"detail": "A positive weight and date are required."}, status=400)
        row = AnimalWeightRecord.objects.create(animal=animal, recorded_on=request.data["recorded_on"], weight_kg=weight, created_by=request.user)
        animal.weight_kg = weight; animal.save(update_fields=["weight_kg"])
        return Response({"id": row.id, "weight_kg": row.weight_kg}, status=201)

class HerdBreedingRecordsView(APIView):
    def post(self, request, herd_id):
        herd, kind = _herd_for(request.user, herd_id), request.data.get("event_type")
        if kind not in dict(LivestockBreedingRecord.TYPES) or not request.data.get("occurred_on"): return Response({"detail": "Valid event type and date are required."}, status=400)
        animal = herd.animals.filter(id=request.data.get("animal_id")).first() if request.data.get("animal_id") else None
        row = LivestockBreedingRecord.objects.create(herd=herd, animal=animal, event_type=kind, occurred_on=request.data["occurred_on"], expected_due_date=request.data.get("expected_due_date") or None, outcome=str(request.data.get("outcome", "")), notes=str(request.data.get("notes", "")), created_by=request.user)
        if animal and kind == "pregnancy_check" and "positive" in row.outcome.lower(): animal.breeding_status = "pregnant"; animal.save(update_fields=["breeding_status"])
        if herd.traceability_batch_id and kind in ["birth", "weaning"]: append_event(batch_id=herd.traceability_batch_id, actor=request.user, event_type=kind, stage=kind, description=row.outcome or row.notes or kind, location=request.user.location, quantity=1, unit="head")
        return Response({"id": row.id}, status=201)

class HerdFinancialRecordsView(APIView):
    def get(self, request, herd_id):
        herd = _herd_for(request.user, herd_id); rows = herd.financial_records.order_by("-occurred_on")
        income = rows.filter(record_type="income").aggregate(total=Sum("amount"))["total"] or Decimal("0"); expenses = rows.filter(record_type="expense").aggregate(total=Sum("amount"))["total"] or Decimal("0")
        return Response({"results": list(rows.values("id", "record_type", "category", "amount", "occurred_on", "description")), "summary": {"income": income, "expenses": expenses, "net": income-expenses}})
    def post(self, request, herd_id):
        herd, kind = _herd_for(request.user, herd_id), request.data.get("record_type")
        try: amount = Decimal(str(request.data.get("amount")))
        except (InvalidOperation, TypeError): return Response({"detail": "Enter a valid amount."}, status=400)
        if kind not in dict(LivestockFinancialRecord.TYPES) or amount <= 0 or not request.data.get("category") or not request.data.get("occurred_on"): return Response({"detail": "Type, category, positive amount, and date are required."}, status=400)
        row = LivestockFinancialRecord.objects.create(herd=herd, record_type=kind, category=str(request.data["category"])[:60], amount=amount, occurred_on=request.data["occurred_on"], description=str(request.data.get("description", "")), created_by=request.user)
        if herd.traceability_batch_id and kind == "income" and row.category.lower() in ["sale", "animal sale"]: append_event(batch_id=herd.traceability_batch_id, actor=request.user, event_type="sale", stage="sale", description=row.description or "Livestock sale recorded.", location=request.user.location, quantity=1, unit="transaction")
        return Response({"id": row.id}, status=201)

class HerdMovementView(APIView):
    def post(self, request, herd_id):
        herd = _herd_for(request.user, herd_id); event_type = request.data.get("event_type")
        if event_type not in ["movement", "transport"] or not request.data.get("location") or not request.data.get("description"): return Response({"detail": "Movement type, location, and description are required."}, status=400)
        if not herd.traceability_batch_id: return Response({"detail": "Traceability is not initialized for this herd."}, status=409)
        uploads = [request.FILES["evidence"]] if request.FILES.get("evidence") else []
        event = append_event(batch_id=herd.traceability_batch_id, actor=request.user, event_type=event_type, stage=event_type, description=str(request.data["description"]), location=str(request.data["location"])[:140], quantity=request.data.get("quantity", herd.head_count), unit="head", uploads=uploads)
        return Response({"id": event.id, "event_hash": event.event_hash}, status=201)

class AnimalWelfareReportsView(APIView):
    def get(self, request): return Response(list(AnimalWelfareReport.objects.filter(reporter=request.user).values("id", "listing_id", "delivery_id", "category", "details", "status", "resolution", "created_at")))
    def post(self, request):
        details, category = str(request.data.get("details", "")).strip(), str(request.data.get("category", "")).strip()
        if not details or not category: return Response({"detail": "Category and details are required."}, status=400)
        row = AnimalWelfareReport.objects.create(reporter=request.user, listing_id=request.data.get("listing_id") or None, delivery_id=request.data.get("delivery_id") or None, category=category[:60], details=details, evidence=request.FILES.get("evidence"))
        for admin in User.objects.filter(is_staff=True, is_active=True): Notification.objects.create(user=admin, type="animal_welfare", title="Animal-welfare report submitted", message=f"Report #{row.id}: {row.category}", action_url="/admin/livestock")
        return Response({"id": row.id, "status": row.status}, status=201)

class LivestockRestrictionsView(APIView):
    def get(self, request):
        today = timezone.localdate(); rows = LivestockMovementRestriction.objects.filter(active=True, starts_on__lte=today).filter(Q(ends_on=None)|Q(ends_on__gte=today))
        return Response(list(rows.values("id", "species", "origin_region", "destination_region", "reason", "starts_on", "ends_on", "source_name", "source_reference")))

class AdminLivestockOperationsView(APIView):
    permission_classes = [permissions.IsAdminUser]
    def get(self, request):
        return Response({"pending_listings": list(LiveAnimalListingDetail.objects.filter(verification_status="pending").values("listing_id", "species", "breed", "health_inspection_date", "movement_permit_reference", "created_at")), "welfare_reports": list(AnimalWelfareReport.objects.values("id", "listing_id", "delivery_id", "category", "status", "assigned_to_id", "created_at")), "restrictions": list(LivestockMovementRestriction.objects.values()), "catalogue": list(LivestockCatalogueEntry.objects.values())})
    def post(self, request):
        action = request.data.get("action")
        if action == "restriction":
            if not request.data.get("reason") or not request.data.get("starts_on") or not request.data.get("source_name"): return Response({"detail": "Reason, start date, and authoritative source are required."}, status=400)
            row = LivestockMovementRestriction.objects.create(species=str(request.data.get("species", "")), origin_region=str(request.data.get("origin_region", "")), destination_region=str(request.data.get("destination_region", "")), reason=str(request.data["reason"]), starts_on=request.data["starts_on"], ends_on=request.data.get("ends_on") or None, source_name=str(request.data["source_name"]), source_reference=str(request.data.get("source_reference", "")), created_by=request.user)
        elif action == "catalogue":
            if request.data.get("entry_type") not in dict(LivestockCatalogueEntry.TYPES) or not request.data.get("name"): return Response({"detail": "Valid catalogue type and name are required."}, status=400)
            row = LivestockCatalogueEntry.objects.create(entry_type=request.data["entry_type"], name=str(request.data["name"])[:120], species=str(request.data.get("species", "")), rules=request.data.get("rules", {}), updated_by=request.user)
        else: return Response({"detail": "Unsupported livestock administration action."}, status=400)
        audit_change(actor=request.user, action=f"livestock.{action}_created", target=row, before={}, after=snapshot(row, [field.name for field in row._meta.fields if field.name not in ["id", "created_at", "updated_at"]]), reason=str(request.data.get("reason", "")))
        return Response({"id": row.id}, status=201)
    def patch(self, request):
        report = generics.get_object_or_404(AnimalWelfareReport, id=request.data.get("report_id")); before = snapshot(report, ["status", "resolution", "assigned_to"])
        if request.data.get("status") not in dict(AnimalWelfareReport.STATUSES): return Response({"detail": "Invalid report status."}, status=400)
        report.status = request.data["status"]; report.resolution = str(request.data.get("resolution", "")); report.assigned_to = request.user; report.save()
        audit_change(actor=request.user, action="livestock.welfare_report_updated", target=report, before=before, after=snapshot(report, ["status", "resolution", "assigned_to"]), reason=report.resolution)
        Notification.objects.create(user=report.reporter, type="animal_welfare", title="Animal-welfare report updated", message=report.resolution or f"Your report is now {report.status}.", action_url="/app/livestock")
        return Response({"id": report.id, "status": report.status})
