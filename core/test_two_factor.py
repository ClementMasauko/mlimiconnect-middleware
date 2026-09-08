from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .two_factor import decrypt_secret, totp


User = get_user_model()


class TwoFactorAuthenticationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="secured", email="secured@example.mw", password="SafePassword!234", email_verified=True)

    def enable_two_factor(self):
        self.client.force_authenticate(self.user)
        setup = self.client.post("/api/auth/2fa/setup/", {}, format="json")
        self.assertEqual(setup.status_code, 200)
        self.user.refresh_from_db()
        secret = decrypt_secret(self.user.two_factor_pending_secret)
        confirmed = self.client.post("/api/auth/2fa/confirm/", {"code": totp(secret)}, format="json")
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(len(confirmed.data["recovery_codes"]), 10)
        self.client.force_authenticate(user=None)
        return confirmed.data["recovery_codes"]

    def test_password_login_requires_second_factor_before_session(self):
        self.enable_two_factor()
        response = self.client.post("/api/auth/login/", {"identifier": self.user.email, "password": "SafePassword!234"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["two_factor_required"])
        self.assertNotIn("sessionid", response.cookies)
        self.user.refresh_from_db()
        completed = self.client.post("/api/auth/2fa/challenge/", {"challenge_token": response.data["challenge_token"], "code": totp(decrypt_secret(self.user.two_factor_secret))}, format="json")
        self.assertEqual(completed.status_code, 200)
        self.assertIn("sessionid", completed.cookies)

    def test_recovery_code_is_single_use(self):
        recovery_codes = self.enable_two_factor()
        login = self.client.post("/api/auth/login/", {"identifier": self.user.email, "password": "SafePassword!234"}, format="json")
        payload = {"challenge_token": login.data["challenge_token"], "code": recovery_codes[0]}
        first = self.client.post("/api/auth/2fa/challenge/", payload, format="json")
        self.assertEqual(first.status_code, 200)
        self.client.logout()
        second = self.client.post("/api/auth/2fa/challenge/", payload, format="json")
        self.assertEqual(second.status_code, 400)

    def test_disable_requires_password_and_current_code(self):
        self.enable_two_factor()
        self.client.force_authenticate(self.user)
        self.user.refresh_from_db()
        response = self.client.post("/api/auth/2fa/disable/", {"password": "SafePassword!234", "code": totp(decrypt_secret(self.user.two_factor_secret))}, format="json")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.two_factor_enabled)
        self.assertEqual(self.user.two_factor_secret, "")
