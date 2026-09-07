import re

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .communications import deliver_security_code
from .models import MessageDelivery, OutboxMessage, PasswordResetRequest, User


class TransactionalOutboxTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("outbox-user", "outbox@example.com", "Strong-pass-123")

    @override_settings(OUTBOX_INLINE=False, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_message_is_durable_before_worker_delivery(self):
        deliver_security_code(self.user, "Security code", "Code 123456", "Code 123456", "security")
        message = OutboxMessage.objects.get(channel="email")
        self.assertEqual(message.status, "pending")
        self.assertFalse(MessageDelivery.objects.exists())
        call_command("process_outbox")
        message.refresh_from_db()
        self.assertEqual(message.status, "sent")
        self.assertEqual(MessageDelivery.objects.get().status, "accepted")
        self.assertEqual(len(mail.outbox), 1)


class PasswordRecoveryHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user("recovery-user", "recovery@example.com", "Strong-pass-123")

    @override_settings(OUTBOX_INLINE=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_resend_cooldown_and_attempt_lock(self):
        payload = {"method": "email", "email": self.user.email}
        first = self.client.post("/api/auth/forgot-password/", payload, format="json")
        self.assertEqual(first.status_code, 200)
        self.client.post("/api/auth/forgot-password/", payload, format="json")
        self.assertEqual(PasswordResetRequest.objects.filter(user=self.user).count(), 1)
        reset = PasswordResetRequest.objects.get(user=self.user)
        code = re.search(r"code is (\d{6})", mail.outbox[-1].body).group(1)
        for _ in range(5):
            response = self.client.post("/api/auth/verify-reset-otp/", {"token": str(reset.token), "otp": "000000"}, format="json")
            self.assertEqual(response.status_code, 400)
        reset.refresh_from_db()
        self.assertEqual(reset.failed_attempts, 5)
        self.assertIsNotNone(reset.locked_at)
        locked = self.client.post("/api/auth/verify-reset-otp/", {"token": str(reset.token), "otp": code}, format="json")
        self.assertEqual(locked.status_code, 400)
