from django.core.management.base import BaseCommand, CommandError

from core.communications import process_pending_outbox


class Command(BaseCommand):
    help = "Deliver pending transactional-outbox messages with retry/backoff."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 1000:
            raise CommandError("--limit must be between 1 and 1000")
        sent = process_pending_outbox(limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Processed outbox batch; {sent} message(s) sent."))
