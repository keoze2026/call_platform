"""Buyer API keys the main account can copy again (CH-490).

Sir, 3 Nov, on Active Bots: "how can Sir copy the existing key?" A key was
kept only as its SHA-256 fingerprint (the API checks a key against it), so
once the "shown once" dialog closed nobody could see it again. A buyer key
issued on Active Bots is now also kept encrypted (Fernet: AES-128 with an
HMAC), and the main account - only it, as every API key (CH-243) - can copy
it from the page. Every copy is written to the activity log.

The encryption key is API_KEY_VAULT_KEY in .env when set, else derived from
SECRET_KEY. Changing either makes the kept copies unreadable (the keys keep
working; the page then asks for a new key to copy one again).

Keys issued before CH-490 were never kept, so they cannot be copied: only a
new key can be.
"""
import base64
import hashlib

from django.conf import settings


def _fernet():
    from cryptography.fernet import Fernet
    secret = getattr(settings, 'API_KEY_VAULT_KEY', '') or settings.SECRET_KEY
    digest = hashlib.sha256(f'avortyx-api-key-vault:{secret}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(raw_key: str) -> str:
    return _fernet().encrypt(raw_key.encode()).decode()


def unseal(sealed: str) -> str | None:
    """The key, or None when it was never kept or cannot be read any more."""
    if not sealed:
        return None
    from cryptography.fernet import InvalidToken
    try:
        return _fernet().decrypt(sealed.encode()).decode()
    except (InvalidToken, ValueError):
        return None
