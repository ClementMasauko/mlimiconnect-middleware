from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import OperationalEvent
class Command(BaseCommand):
    help="Delete operational metrics older than the configured retention window."
    def add_arguments(self,parser):parser.add_argument("--days",type=int,default=30)
    def handle(self,*args,**options):
        if options["days"]<1:raise ValueError("Retention must be at least one day.")
        deleted,_=OperationalEvent.objects.filter(created_at__lt=timezone.now()-timedelta(days=options["days"])).delete();self.stdout.write(self.style.SUCCESS(f"Purged {deleted} operational events."))
