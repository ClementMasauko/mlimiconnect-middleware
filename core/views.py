from django.conf import settings
from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.db.models import Q
from django.core.mail import send_mail
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth import password_validation
from django.utils import timezone
from datetime import timedelta
import secrets
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import AccountDeletionRequest, Dispute, Listing, NewsletterSubscription, NotificationPreference, Order, PasswordResetRequest, USSDCredential, User
from .serializers import CheckoutSerializer, ContactSerializer, ListingSerializer, LoginSerializer, NewsletterSerializer, OrderSerializer, OrganizationSerializer, RegisterSerializer, UserSerializer

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

class ListingListCreate(generics.ListCreateAPIView):
    serializer_class = ListingSerializer
    def get_queryset(self): return Listing.objects.filter(seller=self.request.user).select_related("seller")
    def perform_create(self, serializer):
        if not self.request.user.can_sell: raise PermissionDenied("This account is not enabled to sell.")
        if self.request.user.account_type != "individual" and self.request.user.organization.verification_status != "verified": raise PermissionDenied("Organization verification is required before publishing listings.")
        serializer.save(seller=self.request.user)

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

class DisputeCreate(APIView):
    def post(self, request, order_id):
        order = generics.get_object_or_404(Order, id=order_id, buyer=request.user)
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10: return Response({"detail": "Please provide at least 10 characters."}, status=400)
        dispute = Dispute.objects.create(order=order, opened_by=request.user, reason=reason)
        return Response({"id": dispute.id, "status": dispute.status}, status=201)

class NotificationPreferencesView(APIView):
    def put(self, request):
        pref, _ = NotificationPreference.objects.update_or_create(user=request.user, defaults={"settings": request.data})
        return Response(pref.settings)

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
