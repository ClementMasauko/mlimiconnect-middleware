from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import SimpleTestCase, override_settings

from .storage import AdaptiveCloudinaryStorage


@override_settings(
    MEDIA_STORAGE_PROVIDER="cloudinary",
    CLOUDINARY_URL="cloudinary://test-key:test-secret@test-cloud",
    PROTECTED_MEDIA_URL_TTL_SECONDS=300,
)
class CloudinaryStorageTests(SimpleTestCase):
    @patch("cloudinary.uploader.upload")
    @patch("cloudinary.utils.cloudinary_url", return_value=("https://res.cloudinary.com/test/image/upload/sample.jpg", {}))
    def test_public_image_uses_signed_server_upload_and_optimized_delivery(self, mocked_url, mocked_upload):
        mocked_upload.return_value = {"public_id": "listings/maize", "format": "jpg", "resource_type": "image"}
        storage = AdaptiveCloudinaryStorage(protected=False)
        name = storage.save("listings/maize.jpg", ContentFile(b"jpeg"))
        self.assertEqual(name, "image/listings/maize.jpg")
        self.assertTrue(storage.url(name).startswith("https://"))
        options = mocked_upload.call_args.kwargs
        self.assertEqual(options["type"], "upload")
        self.assertFalse(options["overwrite"])
        self.assertEqual(mocked_url.call_args.kwargs["fetch_format"], "auto")
        self.assertEqual(mocked_url.call_args.kwargs["quality"], "auto")

    @patch("cloudinary.uploader.upload")
    @patch("cloudinary.utils.private_download_url", return_value="https://api.cloudinary.com/download?signed=true")
    def test_protected_document_uses_authenticated_delivery_and_expiring_url(self, mocked_url, mocked_upload):
        mocked_upload.return_value = {"public_id": "organization-documents/certificate.pdf", "resource_type": "raw"}
        storage = AdaptiveCloudinaryStorage(protected=True)
        name = storage.save("organization-documents/certificate.pdf", ContentFile(b"pdf"))
        self.assertEqual(name, "raw/organization-documents/certificate.pdf")
        self.assertIn("signed=true", storage.url(name))
        self.assertEqual(mocked_upload.call_args.kwargs["type"], "authenticated")
        self.assertEqual(mocked_url.call_args.kwargs["type"], "authenticated")
