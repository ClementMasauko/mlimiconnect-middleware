import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet
from django.conf import settings


def _cipher():
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt_secret(secret): return _cipher().encrypt(secret.encode()).decode()
def decrypt_secret(secret): return _cipher().decrypt(secret.encode()).decode()
def generate_secret(): return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret, timestamp=None):
    counter = int((timestamp or time.time()) // 30)
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    digest = hmac.new(base64.b32decode(padded), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff) % 1_000_000
    return f"{value:06d}"


def verify_totp(secret, code):
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit(): return False
    now = time.time()
    return any(hmac.compare_digest(totp(secret, now + offset * 30), code) for offset in (-1, 0, 1))


def provisioning_uri(secret, email):
    issuer = "MlimiConnect"
    return f"otpauth://totp/{quote(issuer)}:{quote(email)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def create_recovery_codes(count=10):
    raw = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}".upper() for _ in range(count)]
    return raw, [_recovery_digest(code) for code in raw]


def _recovery_digest(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.strip().upper().encode(), hashlib.sha256).hexdigest()


def consume_recovery_code(user, code):
    normalized = str(code or "").strip().upper()
    candidate = _recovery_digest(normalized)
    for index, encoded in enumerate(user.two_factor_recovery_codes):
        if hmac.compare_digest(candidate, encoded):
            user.two_factor_recovery_codes.pop(index)
            user.save(update_fields=["two_factor_recovery_codes"])
            return True
    return False
