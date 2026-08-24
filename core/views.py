from django.conf import settings
from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncMonth
from django.core.mail import send_mail
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import password_validation
from django.utils import timezone
from datetime import timedelta
import secrets
from rest_framework import generics, permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import AccountDeletionRequest, AdvisoryUsage, ChatMessage, Conversation, Dispute, ExpertConsultation, Listing, NewsletterSubscription, Notification, NotificationPreference, Order, OrderReview, PasswordResetRequest, PlatformSetting, SmartContract, Subscription, TraceabilityBatch, TraceabilityEvent, USSDCredential, User, WalletTransaction
from .serializers import CheckoutSerializer, ContactSerializer, ConversationSerializer, ListingSerializer, LoginSerializer, MessageSerializer, NewsletterSerializer, NotificationSerializer, OrderReviewSerializer, OrderSerializer, OrganizationSerializer, RegisterSerializer, SubscriptionSerializer, TraceabilityBatchSerializer, UserSerializer

class StandardPagination(PageNumberPagination):
    page_size = 24
    page_size_query_param = "page_size"
    max_page_size = 100

class CsrfView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def get(self, request): return Response({"csrfToken": get_token(request)})

@method_decorator(csrf_protect, name="dispatch")
class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

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
    queryset = Listing.objects.filter(is_active=True).select_related("seller")
    pagination_class = StandardPagination
    def get_queryset(self):
        queryset = super().get_queryset().order_by("-created_at")
        if self.request.query_params.get("category"): queryset = queryset.filter(category=self.request.query_params["category"])
        return queryset

class ListingListCreate(generics.ListCreateAPIView):
    serializer_class = ListingSerializer
    def get_queryset(self): return Listing.objects.filter(seller=self.request.user).select_related("seller")
    def perform_create(self, serializer):
        if not self.request.user.can_sell: raise PermissionDenied("This account is not enabled to sell.")
        if self.request.user.account_type != "individual" and self.request.user.organization.verification_status != "verified": raise PermissionDenied("Organization verification is required before publishing listings.")
        serializer.save(seller=self.request.user)

class ListingDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ListingSerializer
    def get_queryset(self): return Listing.objects.filter(seller=self.request.user)
    def perform_destroy(self, instance): instance.is_active = False; instance.save(update_fields=["is_active"])

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
        if not request.user.can_buy: return Response({"detail": "This account is not enabled to buy."}, status=403)
        if not settings.PAYMENTS_ENABLED:
            return Response({"detail": "Payments are not enabled."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        serializer = CheckoutSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        order = serializer.save()
        return Response({"order": OrderSerializer(order).data, "redirect_url": None}, status=status.HTTP_201_CREATED)

class OrderList(generics.ListAPIView):
    serializer_class = OrderSerializer
    def get_queryset(self): return Order.objects.filter(buyer=self.request.user)

class SellerOrderList(generics.ListAPIView):
    serializer_class = OrderSerializer
    def get_queryset(self): return Order.objects.filter(items__listing__seller=self.request.user).distinct().order_by("-created_at")

class OrderDetail(generics.RetrieveAPIView):
    serializer_class = OrderSerializer
    def get_queryset(self): return Order.objects.filter(Q(buyer=self.request.user) | Q(items__listing__seller=self.request.user)).distinct()

class OrderStatus(APIView):
    def patch(self, request, order_id):
        order = generics.get_object_or_404(Order.objects.filter(items__listing__seller=request.user).distinct(), id=order_id)
        next_status = request.data.get("status")
        allowed = {"pending": ["paid", "cancelled"], "paid": ["fulfilled", "cancelled"]}
        if next_status not in allowed.get(order.status, []): return Response({"detail": "Invalid status transition."}, status=400)
        order.status = next_status; order.save(update_fields=["status"])
        return Response(OrderSerializer(order).data)

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
        event = TraceabilityEvent.objects.create(batch=batch, actor=request.user, stage=request.data.get("stage", "update"), description=request.data.get("description", ""), location=request.data.get("location", ""))
        batch.status = event.stage; batch.save(update_fields=["status", "updated_at"])
        return Response({"id": event.id, "status": batch.status}, status=201)

class TraceabilityVerify(APIView):
    permission_classes = [permissions.AllowAny]; authentication_classes = []
    def get(self, request, batch_code):
        batch = generics.get_object_or_404(TraceabilityBatch.objects.prefetch_related("events"), batch_code=batch_code)
        return Response(TraceabilityBatchSerializer(batch).data)

class SmartContractListCreate(generics.ListCreateAPIView):
    def get(self, request): return Response(list(request.user.smart_contracts.values("id", "name", "terms", "status", "created_at")))
    def post(self, request):
        contract = SmartContract.objects.create(owner=request.user, name=request.data.get("name", "Agricultural agreement"), terms=request.data.get("terms", {}))
        return Response({"id": contract.id, "status": contract.status}, status=201)

class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view): return bool(request.user and request.user.is_authenticated and (request.user.user_type == "admin" or request.user.is_staff))

class AdminOverview(APIView):
    permission_classes = [IsAdmin]
    def get(self, request): return Response({"users": User.objects.count(), "activeListings": Listing.objects.filter(is_active=True).count(), "orders": Order.objects.count(), "volume": Order.objects.aggregate(value=Sum("total"))["value"] or 0, "disputes": Dispute.objects.filter(status="open").count(), "usersByType": list(User.objects.values("account_type").annotate(value=Count("id"))), "revenueTrend": list(Order.objects.annotate(period=TruncMonth("created_at")).values("period").annotate(value=Sum("total"), orders=Count("id")).order_by("period")), "categories": list(Listing.objects.values("category").annotate(value=Count("id")).order_by("-value"))})

class AdminUsers(generics.ListAPIView):
    permission_classes = [IsAdmin]; serializer_class = UserSerializer; pagination_class = StandardPagination
    queryset = User.objects.order_by("-date_joined")

class AdminSettingsView(APIView):
    permission_classes = [IsAdmin]
    def get(self, request): return Response({row.key: row.value for row in PlatformSetting.objects.all()})
    def put(self, request):
        for key, value in request.data.items(): PlatformSetting.objects.update_or_create(key=key, defaults={"value": value})
        return self.get(request)

class DisputeCreate(APIView):
    def post(self, request, order_id):
        order = generics.get_object_or_404(Order, id=order_id, buyer=request.user)
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10: return Response({"detail": "Please provide at least 10 characters."}, status=400)
        dispute = Dispute.objects.create(order=order, opened_by=request.user, reason=reason)
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

PLAN_FEATURES = {"free": ["ai_advisory_limited"], "farmer-plus": ["ai_advisory", "expert_consultations"], "buyer-pro": ["bulk_procurement"], "cooperative": ["ai_advisory", "expert_consultations", "member_management"], "organization": ["ai_advisory", "expert_consultations", "institutional_reports"], "enterprise": ["ai_advisory", "expert_consultations", "institutional_reports", "data_exports"]}
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

class AdvisoryAI(APIView):
    def post(self, request):
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        period = timezone.now().strftime("%Y-%m"); usage, _ = AdvisoryUsage.objects.get_or_create(user=request.user, defaults={"period": period})
        if usage.period != period: usage.period, usage.ai_requests, usage.expert_credits_used = period, 0, 0
        if subscription.plan_id == "free" and usage.ai_requests >= 5: return Response({"detail": "Monthly AI advisory allowance reached."}, status=429)
        usage.ai_requests += 1; usage.save()
        location = str(request.data.get("location", request.user.location or "Malawi"))
        return Response({"recommendations": [{"crop": "Maize", "suitability": 85, "plantingSeason": "Confirm against the local seasonal forecast", "expectedYield": "Depends on seed, soil and rainfall", "marketPrice": "Check the live market feed", "risks": f"Inspect soil and pest conditions in {location}", "smartContract": "Optional traceability and insurance tools may apply"}], "usage": {"ai_requests": usage.ai_requests, "limit": 5 if subscription.plan_id == "free" else None}, "notice": "Decision support only; verify locally before acting."})

class ExpertConsultationCreate(APIView):
    def create(self, request, *args, **kwargs):
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        credits = PLAN_CREDITS.get(subscription.plan_id, 0)
        if subscription.status != "active" or not credits: return Response({"detail": "Your active plan does not include expert consultations."}, status=403)
        period = timezone.now().strftime("%Y-%m"); usage, _ = AdvisoryUsage.objects.get_or_create(user=request.user, defaults={"period": period})
        if usage.period != period: usage.period, usage.ai_requests, usage.expert_credits_used = period, 0, 0
        if usage.expert_credits_used >= credits: return Response({"detail": "Monthly expert credits have been used."}, status=429)
        try: starts_at = timezone.datetime.fromisoformat(str(request.data.get("starts_at", "")).replace("Z", "+00:00"))
        except ValueError: return Response({"detail": "Invalid consultation time."}, status=400)
        consultation = ExpertConsultation.objects.create(user=request.user, expert_id=int(request.data.get("expert_id")), starts_at=starts_at)
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
        send_mail("Confirm MlimiConnect account deletion", f"Your account deletion code is {code}. It expires in 10 minutes. If you did not request this, change your password immediately.", settings.DEFAULT_FROM_EMAIL, [request.user.email])
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

@method_decorator(csrf_exempt, name="dispatch")
class USSDAuthenticateView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        configured_key = settings.USSD_SERVICE_KEY
        supplied_key = request.headers.get("X-USSD-Service-Key", "")
        if not configured_key or not supplied_key or not __import__("secrets").compare_digest(configured_key, supplied_key):
            return Response({"detail": "Unauthorized service."}, status=403)
        phone = str(request.data.get("phone", "")).replace(" ", "").replace("-", "")
        pin = str(request.data.get("pin", ""))
        if not phone.startswith("+265") or not pin.isdigit() or len(pin) != 4:
            return Response({"authenticated": False})
        credentials = USSDCredential.objects.select_related("user").filter(user__phone=phone, user__is_active=True, enabled=True)
        credential = credentials.first() if credentials.count() == 1 else None
        return Response({"authenticated": bool(credential and credential.verify(pin))})

class ForgotPasswordView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    def post(self, request):
        if request.data.get("method") != "email": return Response({"detail": "SMS password reset is not configured. Please use email."}, status=503)
        email = str(request.data.get("email", "")).strip().lower()
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        if user:
            code = f"{secrets.randbelow(1_000_000):06d}"
            reset = PasswordResetRequest.objects.create(user=user, expires_at=timezone.now() + timedelta(minutes=10))
            reset.set_code(code); reset.save(update_fields=["code_hash"])
            link = f"{settings.FRONTEND_URL.rstrip('/')}/reset-password?token={reset.token}"
            send_mail("MlimiConnect password reset", f"Your reset code is {code}. Open {link}. It expires in 10 minutes.", settings.DEFAULT_FROM_EMAIL, [user.email])
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
