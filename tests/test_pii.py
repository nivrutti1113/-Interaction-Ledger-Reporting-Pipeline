"""Unit tests for PII protection: authenticated encryption, deterministic HMAC, and masking."""
from __future__ import annotations

import os
import pytest

from solution.pii import compute_phone_hmac, decrypt_pii, encrypt_pii, mask_phone


class TestPIIProtection:
    def test_encrypt_decrypt_roundtrip(self):
        secret_name = "Aarav Sharma"
        encrypted = encrypt_pii(secret_name)
        assert encrypted != secret_name
        assert "Aarav" not in encrypted
        assert "Sharma" not in encrypted

        decrypted = decrypt_pii(encrypted)
        assert decrypted == secret_name

    def test_empty_string_handling(self):
        assert encrypt_pii("") == ""
        assert decrypt_pii("") == ""
        assert encrypt_pii(None) == ""
        assert decrypt_pii(None) == ""

    def test_hmac_deterministic_matching(self):
        phone1 = "+919876543210"
        phone2 = "+919876543210"
        diff_phone = "+919876543211"

        hmac1 = compute_phone_hmac(phone1)
        hmac2 = compute_phone_hmac(phone2)
        hmac_diff = compute_phone_hmac(diff_phone)

        assert hmac1 == hmac2
        assert hmac1 != hmac_diff
        assert len(hmac1) == 64  # SHA-256 hex string

    def test_phone_masking(self):
        assert mask_phone("+919876543210") == "+91******3210"
        assert mask_phone("+916463745499") == "+91******5499"
        assert mask_phone("") == ""
        assert mask_phone(None) == ""
