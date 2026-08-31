from django.test import SimpleTestCase

from .sentry import before_send, redact_text, scrub


class SentryPrivacyTests(SimpleTestCase):
    def test_text_and_structured_secrets_are_redacted(self):
        text = redact_text("clement@example.com +265886096459 token=abc123")
        self.assertNotIn("clement@example.com", text)
        self.assertNotIn("+265886096459", text)
        self.assertNotIn("abc123", text)
        self.assertEqual(scrub({"password": "secret", "safe": "operation"}), {"password": "[REDACTED]", "safe": "operation"})

    def test_before_send_drops_request_user_and_breadcrumbs(self):
        event = {"request": {"data": "private"}, "user": {"email": "person@example.com"}, "breadcrumbs": {"values": [{"message": "private"}]}, "exception": {"values": [{"value": "Call +265886096459"}]}, "extra": {"email": "person@example.com"}}
        cleaned = before_send(event, {})
        self.assertNotIn("request", cleaned); self.assertNotIn("user", cleaned); self.assertNotIn("breadcrumbs", cleaned)
        self.assertNotIn("+265886096459", cleaned["exception"]["values"][0]["value"])
        self.assertEqual(cleaned["extra"]["email"], "[REDACTED]")
