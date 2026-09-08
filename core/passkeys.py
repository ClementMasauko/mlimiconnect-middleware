import json
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from webauthn import generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response, verify_registration_response
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement

from .models import PasskeyChallenge, PasskeyCredential


def _options_payload(options):
    return json.loads(options_to_json(options))


def registration_options(user):
    options = generate_registration_options(
        rp_id=settings.PASSKEY_RP_ID, rp_name=settings.PASSKEY_RP_NAME,
        user_id=str(user.id).encode(), user_name=user.email, user_display_name=user.get_full_name() or user.username,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id)) for row in user.passkeys.all()],
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
    )
    ceremony = PasskeyChallenge.objects.create(user=user, purpose="register", challenge=options.challenge, expires_at=timezone.now() + timedelta(minutes=5))
    return str(ceremony.token), _options_payload(options)


def authentication_options(user):
    credentials = list(user.passkeys.all())
    options = generate_authentication_options(
        rp_id=settings.PASSKEY_RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id)) for row in credentials],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    ceremony = PasskeyChallenge.objects.create(user=user, purpose="authenticate", challenge=options.challenge, expires_at=timezone.now() + timedelta(minutes=5))
    return str(ceremony.token), _options_payload(options)


def credential_id_from_response(response):
    return str(response.get("id", "")) if isinstance(response, dict) else ""


def encode_credential_id(value):
    return bytes_to_base64url(value)


def consume_challenge(token, purpose):
    ceremony = PasskeyChallenge.objects.select_for_update().filter(token=token, purpose=purpose, used=False, expires_at__gt=timezone.now()).first()
    if not ceremony:
        raise ValueError("This passkey request expired or was already used.")
    ceremony.used = True
    ceremony.save(update_fields=["used"])
    return ceremony


def verify_registration(ceremony, response):
    return verify_registration_response(credential=response, expected_challenge=bytes(ceremony.challenge), expected_rp_id=settings.PASSKEY_RP_ID, expected_origin=settings.PASSKEY_ORIGINS, require_user_verification=True)


def verify_authentication(ceremony, credential, response):
    return verify_authentication_response(credential=response, expected_challenge=bytes(ceremony.challenge), expected_rp_id=settings.PASSKEY_RP_ID, expected_origin=settings.PASSKEY_ORIGINS, credential_public_key=bytes(credential.public_key), credential_current_sign_count=credential.sign_count, require_user_verification=True)
