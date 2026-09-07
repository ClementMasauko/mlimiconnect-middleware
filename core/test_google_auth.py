from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from .models import User


@override_settings(GOOGLE_CLIENT_ID="web-client.apps.googleusercontent.com")
class GoogleLoginTests(APITestCase):
    def setUp(self):
        self.client = APIClient(enforce_csrf_checks=True)
        self.csrf = self.client.get("/api/csrf/").data["csrfToken"]

    def google_login(self, claims):
        with patch("core.views.google_id_token.verify_oauth2_token", return_value=claims):
            return self.client.post(
                "/api/auth/google/", {"credential": "signed-google-id-token"},
                format="json", HTTP_X_CSRFTOKEN=self.csrf,
            )

    def test_creates_verified_buyer_and_session_from_verified_google_identity(self):
        response = self.google_login({
            "sub": "google-subject-1", "email": "new.user@gmail.com", "email_verified": True,
        })
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(google_subject="google-subject-1")
        self.assertEqual(user.email, "new.user@gmail.com")
        self.assertTrue(user.email_verified)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.google_onboarding_completed)
        self.assertTrue(response.data["user"]["requires_onboarding"])
        self.assertIn("sessionid", response.cookies)

    def test_links_authoritative_gmail_to_existing_account(self):
        existing = User.objects.create_user(username="existing", email="owner@gmail.com", password="safe-password-123")
        response = self.google_login({
            "sub": "google-subject-2", "email": "owner@gmail.com", "email_verified": True,
        })
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.google_subject, "google-subject-2")

    def test_does_not_silently_link_non_authoritative_existing_email(self):
        User.objects.create_user(username="existing", email="owner@example.mw", password="safe-password-123")
        response = self.google_login({
            "sub": "google-subject-3", "email": "owner@example.mw", "email_verified": True,
        })
        self.assertEqual(response.status_code, 409)

    @patch("core.views.google_id_token.verify_oauth2_token", side_effect=ValueError("invalid"))
    def test_rejects_invalid_google_token(self, _verify):
        response = self.client.post(
            "/api/auth/google/", {"credential": "invalid-token"},
            format="json", HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertEqual(response.status_code, 400)

    def test_new_google_user_completes_seller_onboarding(self):
        response = self.google_login({"sub": "onboard-sub", "email": "farmer@gmail.com", "email_verified": True})
        self.assertEqual(response.status_code, 200)
        csrf = response.cookies["csrftoken"].value
        completed = self.client.post(
            "/api/auth/google/onboarding/",
            {"account_type": "individual", "trading_mode": "both", "phone": "+265999123456", "location": "Lilongwe"},
            format="json", HTTP_X_CSRFTOKEN=csrf,
        )
        self.assertEqual(completed.status_code, 200)
        user = User.objects.get(google_subject="onboard-sub")
        self.assertTrue(user.google_onboarding_completed)
        self.assertTrue(user.can_buy)
        self.assertTrue(user.can_sell)
        self.assertEqual(user.user_type, "farmer")

    def test_authenticated_user_can_link_and_unlink_matching_google_account(self):
        user = User.objects.create_user(username="local", email="local@example.mw", password="safe-password-123")
        self.client.force_authenticate(user)
        with patch("core.views.google_id_token.verify_oauth2_token", return_value={"sub": "linked-sub", "email": "local@example.mw", "email_verified": True}):
            linked = self.client.post("/api/auth/google/link/", {"credential": "signed"}, format="json")
        self.assertEqual(linked.status_code, 200)
        unlinked = self.client.post("/api/auth/google/unlink/", {"password": "safe-password-123"}, format="json")
        self.assertEqual(unlinked.status_code, 200)
        user.refresh_from_db()
        self.assertIsNone(user.google_subject)

    @patch("core.views.deliver_security_code")
    def test_google_only_user_can_start_deletion_with_google_reauthentication(self, _deliver):
        user = User.objects.create_user(username="google-delete", email="delete@gmail.com", google_subject="delete-sub")
        user.set_unusable_password(); user.save(update_fields=["password"])
        self.client.force_authenticate(user)
        with patch("core.views.google_id_token.verify_oauth2_token", return_value={"sub": "delete-sub", "email": "delete@gmail.com", "email_verified": True}):
            response = self.client.post("/api/users/account", {"google_credential": "signed"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("token", response.data)

    def test_security_summary_reports_connected_methods_and_activity(self):
        user = User.objects.create_user(username="secure", email="secure@gmail.com", password="safe-password-123", google_subject="secure-sub")
        self.client.force_authenticate(user)
        response = self.client.get("/api/auth/security/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["google_connected"])
        self.assertTrue(response.data["has_usable_password"])
