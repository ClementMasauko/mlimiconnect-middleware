import posixpath
import time
import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "avif"}


@deconstructible
class AdaptiveCloudinaryStorage(Storage):
    """Cloudinary in deployments, local filesystem in development and tests."""

    def __init__(self, protected=False):
        self.protected = protected

    @property
    def cloudinary_enabled(self):
        return getattr(settings, "MEDIA_STORAGE_PROVIDER", "") == "cloudinary" and bool(getattr(settings, "CLOUDINARY_URL", ""))

    @property
    def local(self):
        return FileSystemStorage(location=settings.MEDIA_ROOT, base_url=settings.MEDIA_URL)

    def _parts(self, stored_name):
        kind, _, value = str(stored_name).partition("/")
        if kind not in {"image", "raw"} or not value:
            raise ValueError("Invalid Cloudinary asset reference.")
        suffix = PurePosixPath(value).suffix.lstrip(".").lower()
        public_id = value if kind == "raw" else value[:-(len(suffix) + 1)] if suffix else value
        return kind, public_id, suffix

    def get_available_name(self, name, max_length=None):
        path = PurePosixPath(str(name).replace("\\", "/"))
        safe_stem = "".join(char if char.isalnum() or char in "-_" else "-" for char in path.stem)[:40] or "asset"
        suffix = path.suffix.lower()
        folder = "/".join(part for part in path.parent.parts if part not in {".", ".."})
        unique = f"{safe_stem}-{uuid.uuid4().hex[:16]}{suffix}"
        return posixpath.join(folder, unique) if folder else unique

    def _save(self, name, content):
        if not self.cloudinary_enabled:
            return self.local.save(name, content)
        import cloudinary.uploader
        suffix = PurePosixPath(name).suffix.lstrip(".").lower()
        resource_type = "image" if suffix in IMAGE_EXTENSIONS else "raw"
        public_id = str(PurePosixPath(name).with_suffix("")) if resource_type == "image" else name
        content.seek(0)
        result = cloudinary.uploader.upload(
            content, public_id=public_id, resource_type=resource_type,
            type="authenticated" if self.protected else "upload",
            overwrite=False, unique_filename=False, use_filename=False,
            tags=["mlimiconnect", "protected" if self.protected else "public"],
        )
        saved_id = str(result.get("public_id") or public_id)
        saved_format = str(result.get("format") or suffix)
        if resource_type == "image" and saved_format and not saved_id.endswith(f".{saved_format}"):
            saved_id = f"{saved_id}.{saved_format}"
        return f"{resource_type}/{saved_id}"

    def url(self, name):
        if not self.cloudinary_enabled:
            return self.local.url(name)
        if not str(name).startswith(("image/", "raw/")):
            # Preserve readable legacy filesystem references during migration;
            # callers receive the old media URL instead of a server error.
            return self.local.url(name)
        import cloudinary.utils
        resource_type, public_id, file_format = self._parts(name)
        delivery_type = "authenticated" if self.protected else "upload"
        if self.protected:
            return cloudinary.utils.private_download_url(
                public_id, file_format or None, resource_type=resource_type,
                type=delivery_type, expires_at=int(time.time()) + settings.PROTECTED_MEDIA_URL_TTL_SECONDS,
                attachment=False,
            )
        if resource_type == "raw":
            url, _ = cloudinary.utils.cloudinary_url(public_id, resource_type="raw", type=delivery_type, secure=True)
        else:
            url, _ = cloudinary.utils.cloudinary_url(public_id, format=file_format or None, resource_type="image", type=delivery_type, secure=True, fetch_format="auto", quality="auto")
        return url

    def delete(self, name):
        if not self.cloudinary_enabled:
            return self.local.delete(name)
        if not str(name).startswith(("image/", "raw/")):
            return self.local.delete(name)
        import cloudinary.uploader
        resource_type, public_id, _file_format = self._parts(name)
        cloudinary.uploader.destroy(public_id, resource_type=resource_type, type="authenticated" if self.protected else "upload", invalidate=True)

    def exists(self, name):
        return self.local.exists(name) if not self.cloudinary_enabled else False


public_media_storage = AdaptiveCloudinaryStorage(protected=False)
protected_media_storage = AdaptiveCloudinaryStorage(protected=True)
