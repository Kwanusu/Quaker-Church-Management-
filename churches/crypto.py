"""
Encryption for secrets stored in the database (YouTube stream keys; later,
M-Pesa/Stripe credentials). Uses Fernet (AES-128-CBC + HMAC).

Key: settings.FIELD_ENCRYPTION_KEY if set (recommended in production: a
separate secret, so a leaked SECRET_KEY alone doesn't expose stream keys),
otherwise derived from SECRET_KEY.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    secret = getattr(settings, 'FIELD_ENCRYPTION_KEY', '') or settings.SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(f'field-encryption:{secret}'.encode()).digest())
    return Fernet(key)


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode() if value else ''


def decrypt(token):
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ''  # key changed: the Clerk simply re-enters the stream key
