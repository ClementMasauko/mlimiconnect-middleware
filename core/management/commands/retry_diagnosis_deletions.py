from django.core.management.base import BaseCommand
from django.utils import timezone

from core.diagnosis import DiagnosisError, delete_remote
from core.models import CropDiagnosis


class Command(BaseCommand):
    help = "Retry provider-side deletion for crop diagnoses whose local data has already been erased."

    def handle(self, *args, **options):
        completed = failed = 0
        for diagnosis in CropDiagnosis.objects.filter(status="deletion_pending").exclude(provider_reference="").iterator():
            try:
                delete_remote(diagnosis.provider_reference)
            except DiagnosisError:
                failed += 1
                continue
            diagnosis.remote_deleted = True
            diagnosis.status = "deleted"
            diagnosis.deleted_at = diagnosis.deleted_at or timezone.now()
            diagnosis.save(update_fields=["remote_deleted", "status", "deleted_at"])
            completed += 1
        self.stdout.write(f"Provider deletions completed: {completed}; still pending: {failed}.")
