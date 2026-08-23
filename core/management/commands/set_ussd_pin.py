from django.core.management.base import BaseCommand, CommandError
from core.models import USSDCredential, User

class Command(BaseCommand):
    help = "Set or replace a user's hashed four-digit USSD PIN."
    def add_arguments(self, parser):
        parser.add_argument("identifier", help="Username, email, or phone")
        parser.add_argument("pin")
    def handle(self, identifier, pin, **_options):
        user = User.objects.filter(username=identifier).first() or User.objects.filter(email=identifier).first() or User.objects.filter(phone=identifier).first()
        if not user: raise CommandError("User not found.")
        credential, _ = USSDCredential.objects.get_or_create(user=user)
        try: credential.set_pin(pin)
        except ValueError as error: raise CommandError(str(error)) from error
        credential.enabled = True
        credential.save()
        self.stdout.write(self.style.SUCCESS(f"USSD PIN updated for {user.username}."))
