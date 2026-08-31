import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from .communications import deliver_sms
from .models import MessageDelivery, User


class _ProviderResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps({"data": {"success": True, "smsBatchId": "batch-test-1"}}).encode()


class CommunicationsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("sms-user", "sms@example.mw", "strong-password", phone="+265999123456")

    @override_settings(SMS_ENABLED=True, SMS_PROVIDER="textbee", TEXTBEE_API_KEY="test-key", TEXTBEE_API_URL="https://api.textbee.dev/api/v1", TEXTBEE_DEVICE_ID="device-test")
    @patch("core.communications.urlopen", return_value=_ProviderResponse())
    def test_textbee_acceptance_is_tracked_without_message_or_full_phone(self, mocked):
        delivery = deliver_sms(self.user, "Secret code 123456", "security", essential=True)
        self.assertEqual(delivery.status, "accepted")
        self.assertEqual(delivery.provider_reference, "batch-test-1")
        self.assertEqual(delivery.recipient_hint, "***3456")
        self.assertNotIn("123456", str(MessageDelivery.objects.values().first()))
        request = mocked.call_args.args[0]
        self.assertEqual(request.headers["User-agent"], "MlimiConnect/1.0")

    def test_disabled_sms_is_recorded_as_skipped(self):
        delivery = deliver_sms(self.user, "Account notice", "security", essential=True)
        self.assertEqual(delivery.status, "skipped")
        self.assertEqual(delivery.error_code, "provider_not_configured")
