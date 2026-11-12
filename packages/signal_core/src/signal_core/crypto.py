"""Field-level encryption for stored credentials and contact values (S8).

The key lives only in the environment (SECRETS_ENCRYPTION_KEY, a Fernet key); the database holds
ciphertext. Rotation: set SECRETS_ENCRYPTION_KEY to "new,old" — new values are written with the
first key and every listed key can still read.
"""

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from signal_core.config import get_settings

PREFIX = "enc:v1:"


class SecretsNotConfigured(RuntimeError):
    """SECRETS_ENCRYPTION_KEY is not set, so stored secrets cannot be written or read."""


def _keys() -> list[bytes]:
    setting = get_settings().secrets_encryption_key
    raw = setting.get_secret_value() if setting else ""
    keys = [k.strip().encode() for k in raw.split(",") if k.strip()]
    if not keys:
        raise SecretsNotConfigured("SECRETS_ENCRYPTION_KEY is not set")
    return keys


def _fernet() -> MultiFernet:
    return MultiFernet([Fernet(k) for k in _keys()])


def encrypt_secret(plain: str) -> str:
    return PREFIX + _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(stored: str) -> str:
    if not stored.startswith(PREFIX):
        raise ValueError("stored secret is not in the encrypted format")
    try:
        return _fernet().decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise ValueError("stored secret cannot be decrypted with the configured key") from exc


def lookup_hash(value: str) -> str:
    """Keyed, deterministic digest of a normalised value: lets equal values be found without
    storing them in the clear. Always computed with the oldest listed key so it survives rotation."""  # noqa: E501
    return hmac.new(_keys()[-1], value.strip().lower().encode(), hashlib.sha256).hexdigest()


def generate_secret() -> str:
    return secrets.token_urlsafe(32)
