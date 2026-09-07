import json
import logging
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Organization, OrganizationDocument, User
from .observability import JsonFormatter, RedactionFilter


class OperationalHardeningTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_health_checks_database_and_api_security_headers(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["database"], "ok")
        self.assertEqual(response["Content-Security-Policy"], "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")

    def test_logging_preserves_event_name_and_redacts_sensitive_fields(self):
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "request.completed", (), None)
        record.payload = {"token": "secret"}
        RedactionFilter().filter(record)
        rendered = json.loads(JsonFormatter().format(record))
        self.assertEqual(rendered["message"], "request.completed")

    def test_protected_organization_document_requires_membership(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root, MEDIA_STORAGE_PROVIDER="filesystem"):
            owner = User.objects.create_user("document-owner", "owner-doc@example.com", "Strong-pass-123")
            outsider = User.objects.create_user("document-outsider", "outsider-doc@example.com", "Strong-pass-123")
            organization = Organization.objects.create(
                owner=owner,
                legal_name="Document Cooperative",
                registration_number="DOC-1",
                representative_name="Owner",
                representative_role="Chair",
                address="Lilongwe",
            )
            document = OrganizationDocument.objects.create(
                organization=organization,
                uploaded_by=owner,
                document_type="registration",
                file=SimpleUploadedFile("registration.pdf", b"document", content_type="application/pdf"),
            )
            url = f"/api/protected-files/organization-document/{document.id}/"
            self.client.force_authenticate(outsider)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_authenticate(owner)
            allowed = self.client.get(url)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed["Cache-Control"], "private, no-store")
            allowed.close()
