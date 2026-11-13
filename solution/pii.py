"""PII protection module: authenticated encryption, deterministic HMAC, and phone masking."""
from __future__ import annotations

import hashlib
import hmac
from cryptography.fernet import Fernet

from solution.config import get_pii_encryption_key, get_phone_hmac_key

_cipher: Fernet | None = None
_cached_key: bytes | None = None


def get_cipher() -> Fernet:
    """Return a Fernet cipher instance using the configured encryption key."""
    global _cipher, _cached_key
    key = get_pii_encryption_key()
    if _cipher is None or _cached_key != key:
        _cached_key = key
        _cipher = Fernet(key)
    return _cipher


def encrypt_pii(plaintext: str | None) -> str:
    """Encrypt plaintext string using authenticated Fernet (AES-128-CBC + HMAC)."""
    if plaintext is None or plaintext == "":
        return ""
    cipher = get_cipher()
    return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_pii(ciphertext: str | None) -> str:
    """Decrypt Fernet ciphertext back to plaintext."""
    if ciphertext is None or ciphertext == "":
        return ""
    cipher = get_cipher()
    return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def compute_phone_hmac(canonical_phone: str | None) -> str:
    """
    Compute deterministic keyed HMAC-SHA256 for a canonical phone (+91XXXXXXXXXX).
    This allows blind index lookup and uniqueness constraints without plaintext exposure.
    """
    if canonical_phone is None or canonical_phone == "":
        return ""
    key = get_phone_hmac_key()
    return hmac.new(key, canonical_phone.encode("utf-8"), hashlib.sha256).hexdigest()


def mask_phone(canonical_phone: str | None) -> str:
    """
    Mask a canonical phone number per spec: +91****** followed by last 4 digits.
    Example: +919876543210 -> +91******3210
    """
    if not canonical_phone:
        return ""
    digits_only = "".join(ch for ch in canonical_phone if ch.isdigit())
    if len(digits_only) >= 4:
        last4 = digits_only[-4:]
        return f"+91******{last4}"
    return canonical_phone
