from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from core.models import Listing

class Command(BaseCommand):
    help = "Deactivate listings whose marketplace or product expiry has passed."
    def handle(self, *args, **options):
        count = Listing.objects.filter(is_active=True).filter(Q(listing_expires_at__lte=timezone.now()) | Q(expiry_date__lt=timezone.localdate())).update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} listings."))
