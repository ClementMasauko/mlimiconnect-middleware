from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from django.core import mail
import re
from .models import Organization, USSDCredential, User

class ApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="farmer", email="farmer@example.mw", password="strong-pass-123", user_type="farmer")
        self.client = APIClient(enforce_csrf_checks=True)

    def test_login_uses_session_cookie_without_returning_tokens(self):
        csrf = self.client.get("/api/csrf/").data["csrfToken"]
        response = self.client.post("/api/auth/login/", {"identifier": "farmer@example.mw", "password": "strong-pass-123"}, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("tokens", response.data)
        self.assertIn("sessionid", response.cookies)

    def test_listing_requires_authentication(self):
        response = self.client.post("/api/marketplace/listings/", {"name": "Maize"}, format="json")
        self.assertIn(response.status_code, [401, 403])

    def test_contact_is_persisted(self):
        response = self.client.post("/api/contact/", {"name": "Alick", "email": "a@example.mw", "subject": "Help", "message": "Please contact me."}, format="json")
        self.assertEqual(response.status_code, 201)

    def test_cooperative_can_register_to_buy_and_sell(self):
        csrf = self.client.get("/api/csrf/").data["csrfToken"]
        response = self.client.post("/api/auth/register/", {
            "username": "central_coop", "email": "coop@example.mw", "phone": "+265999123456",
            "password": "strong-coop-pass-123", "user_type": "organization", "account_type": "cooperative", "trading_mode": "both",
            "organization": {"legal_name": "Central Farmers Cooperative", "registration_number": "COOP-001", "representative_name": "Mary Phiri", "representative_role": "Chairperson", "business_size": "medium", "member_count": 120, "address": "Lilongwe"},
        }, format="json", HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username="central_coop")
        self.assertTrue(user.can_buy)
        self.assertTrue(user.can_sell)
        self.assertEqual(Organization.objects.get(owner=user).verification_status, "pending")

    def test_buy_only_account_cannot_create_listing(self):
        buyer = User.objects.create_user(username="buy_only", email="buy@example.mw", password="strong-pass-123", can_buy=True, can_sell=False)
        self.client.force_authenticate(buyer)
        response = self.client.post("/api/marketplace/listings/", {"name": "Seed", "description": "Certified seed", "price": "1000", "quantity": 2, "category": "seed"}, format="json")
        self.assertEqual(response.status_code, 403)

    @override_settings(USSD_SERVICE_KEY="test-service-secret")
    def test_ussd_authentication_requires_service_key_and_hashed_pin(self):
        self.user.phone = "+265999000000"
        self.user.save(update_fields=["phone"])
        credential = USSDCredential(user=self.user)
        credential.set_pin("1234")
        credential.save()
        denied = self.client.post("/api/ussd/authenticate", {"phone": self.user.phone, "pin": "1234"}, format="json")
        self.assertEqual(denied.status_code, 403)
        wrong = self.client.post("/api/ussd/authenticate", {"phone": self.user.phone, "pin": "0000"}, format="json", HTTP_X_USSD_SERVICE_KEY="test-service-secret")
        self.assertEqual(wrong.data, {"authenticated": False})
        allowed = self.client.post("/api/ussd/authenticate", {"phone": self.user.phone, "pin": "1234"}, format="json", HTTP_X_USSD_SERVICE_KEY="test-service-secret")
        self.assertEqual(allowed.data, {"authenticated": True})

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", FRONTEND_URL="http://frontend.test")
    def test_email_password_reset_is_single_use(self):
        requested = self.client.post("/api/auth/forgot-password/", {"method": "email", "email": self.user.email}, format="json")
        self.assertEqual(requested.status_code, 200)
        body = mail.outbox[0].body
        code = re.search(r"code is (\d{6})", body).group(1)
        token = re.search(r"token=([0-9a-f-]+)", body).group(1)
        self.assertEqual(self.client.post("/api/auth/verify-reset-otp/", {"otp": code, "token": token}, format="json").status_code, 200)
        changed = self.client.post("/api/auth/reset-password/", {"otp": code, "token": token, "password": "new-strong-pass-456"}, format="json")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.post("/api/auth/reset-password/", {"otp": code, "token": token, "password": "another-pass-789"}, format="json").status_code, 400)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_account_deletion_requires_password_and_email_code(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.post("/api/users/account", {"password": "wrong"}, format="json").status_code, 400)
        requested = self.client.post("/api/users/account", {"password": "strong-pass-123"}, format="json")
        self.assertEqual(requested.status_code, 200)
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        self.assertEqual(self.client.delete("/api/users/account", {"token": requested.data["token"], "otp": "000000"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete("/api/users/account", {"token": requested.data["token"], "otp": code}, format="json").status_code, 204)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
