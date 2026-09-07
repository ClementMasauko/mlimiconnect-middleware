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
