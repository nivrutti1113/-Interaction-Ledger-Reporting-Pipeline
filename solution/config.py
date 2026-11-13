"""Configuration and secret management for the Assignment A pipeline."""
from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# IST timezone: UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Base directories
DEFAULT_SCHEMAS_DIR = PROJECT_ROOT / "assignment-a-data" / "schemas"
SCHEMAS_DIR = Path(os.getenv("SCHEMAS_DIR", str(DEFAULT_SCHEMAS_DIR)))

DEFAULT_DB_PATH = PROJECT_ROOT / "agentic.db"
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(DEFAULT_DB_PATH)))

# PII and HMAC Secrets
# 32 url-safe base64-encoded bytes for Fernet encryption key
# Default dev keys provided for seamless out-of-the-box local testing if not in environment
DEV_PII_ENCRYPTION_KEY = b"kXz9v_N7gP2r1sT0uV3wX6yZ8aB4cE2dG1hJ3kL5mN8="
DEV_PHONE_HMAC_KEY = b"dev-phone-hmac-secret-key-32bytes!!"


def get_pii_encryption_key() -> bytes:
    """Retrieve the Fernet encryption key from environment or fallback to dev key."""
    env_key = os.getenv("PII_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode("utf-8") if isinstance(env_key, str) else env_key
    return DEV_PII_ENCRYPTION_KEY


def get_phone_hmac_key() -> bytes:
    """Retrieve the phone HMAC key from environment or fallback to dev key."""
    env_key = os.getenv("PHONE_HMAC_KEY")
    if env_key:
        return env_key.encode("utf-8") if isinstance(env_key, str) else env_key
    return DEV_PHONE_HMAC_KEY
