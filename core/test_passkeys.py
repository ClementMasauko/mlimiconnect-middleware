from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from webauthn.helpers import bytes_to_base64url

from core.models import PasskeyChallenge, PasskeyCredential, User
from core.passkeys import consume_challenge


@override_settings(PASSKEY_RP_ID="localhost", PASSKEY_ORIGINS=["http://localhost:5173"])
class PasskeyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="passkey-user", email="passkey@example.invalid", password="safe-password")
        self.client = APIClient()

    @patch("core.views.verify_registration")
    def test_authenticated_user_can_register_a_passkey(self, verify):
        verify.return_value = SimpleNamespace(credential_id=b"credential-one", credential_public_key=b"public-key", sign_count=0, credential_device_type=SimpleNamespace(value="multi_device"), credential_backed_up=True)
        self.client.force_login(self.user)
        begin = self.client.post("/api/auth/passkeys/register/options/", {}, format="json")
        self.assertEqual(begin.status_code, 200)
        complete = self.client.post("/api/auth/passkeys/register/verify/", {"challenge_token": begin.data["challenge_token"], "name": "Laptop", "credential": {"id": "response-id", "response": {"transports": ["internal"]}}}, format="json")
        self.assertEqual(complete.status_code, 201)
        credential = self.user.passkeys.get()
        self.assertEqual(credential.name, "Laptop")
        self.assertTrue(credential.backed_up)

    @patch("core.views.verify_authentication")
    def test_passkey_login_creates_session_and_challenge_cannot_replay(self, verify):
        credential = PasskeyCredential.objects.create(user=self.user, credential_id=bytes_to_base64url(b"credential-two"), public_key=b"public-key")
        verify.return_value = SimpleNamespace(new_sign_count=3, credential_backed_up=False)
        begin = self.client.post("/api/auth/passkeys/authenticate/options/", {"email": self.user.email}, format="json")
        payload = {"challenge_token": begin.data["challenge_token"], "credential": {"id": credential.credential_id, "response": {}}}
        complete = self.client.post("/api/auth/passkeys/authenticate/verify/", payload, format="json")
        self.assertEqual(complete.status_code, 200)
        credential.refresh_from_db()
        self.assertEqual(credential.sign_count, 3)
        self.assertTrue(self.user.auth_sessions.exists())
        replay = self.client.post("/api/auth/passkeys/authenticate/verify/", payload, format="json")
        self.assertEqual(replay.status_code, 400)

    def test_challenge_is_one_use(self):
        challenge = PasskeyChallenge.objects.create(user=self.user, purpose="register", challenge=b"fresh", expires_at=__import__("django.utils.timezone", fromlist=["now"]).now() + __import__("datetime").timedelta(minutes=1))
        consume_challenge(challenge.token, "register")
        with self.assertRaisesMessage(ValueError, "expired or was already used"):
            consume_challenge(challenge.token, "register")

    def test_last_sign_in_method_cannot_be_removed(self):
        self.user.set_unusable_password(); self.user.save(update_fields=["password"])
        credential = PasskeyCredential.objects.create(user=self.user, credential_id="only-passkey", public_key=b"public-key")
        self.client.force_login(self.user)
        response = self.client.delete(f"/api/auth/passkeys/{credential.id}/")
        self.assertEqual(response.status_code, 409)
        self.assertTrue(PasskeyCredential.objects.filter(id=credential.id).exists())
