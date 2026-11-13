"""Normalization utilities for headers, phone numbers, dates, decimals, enums, and timestamps."""
from __future__ import annotations

import re
from datetime import datetime
from solution.config import IST


def normalize_header(header: str) -> str:
    """
    Normalise a header: lower-case it, trim it, and replace every run of '_' or
    whitespace with a single space.
    """
    if not header:
        return ""
    return re.sub(r"[\s_]+", " ", header.lower().strip())


def normalize_phone(raw: str | None) -> str | None:
    """
    Indian mobile number normalization per spec:
    - Strip every non-digit.
    - If 12 digits starting with 91, drop the 91.
    - If 11 digits starting with 0, drop the 0.
    - Valid only if exactly 10 digits and starts with 6, 7, 8 or 9.
    - Store canonically as +91XXXXXXXXXX.
    Returns canonical phone string, or None if invalid.
    """
    if raw is None:
        return None
    raw_str = str(raw).strip()
    if not raw_str:
        return None

    digits = re.sub(r"\D", "", raw_str)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in ("6", "7", "8", "9"):
        return f"+91{digits}"
    return None


def parse_date(raw: str | None) -> str | None:
    """
    Parse date according to spec:
    - Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY (case-insensitive month).
    - Dates are day-first.
    - Impossible dates (like 31/02) are invalid.
    Returns canonical ISO string 'YYYY-MM-DD', or None if invalid.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Handle DD-Mon-YYYY (e.g. 01-Dec-2026, 01-dec-2026, 1-Dec-2026)
    mon_match = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s)
    if mon_match:
        day_str, mon_str, year_str = mon_match.groups()
        s_title = f"{int(day_str):02d}-{mon_str.capitalize()}-{year_str}"
        try:
            dt = datetime.strptime(s_title, "%d-%b-%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    # Try remaining standard formats in day-first order
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def parse_decimal(raw: str | None) -> float | None:
    """
    Parse decimal currency amount per spec:
    - Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    - Commas are thousands separators.
    - Must be >= 0.
    - Round to 2 places.
    Returns rounded float, or None if invalid.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Strip prefixes: ₹, Rs, Rs., INR (case-insensitive)
    s = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", s, flags=re.IGNORECASE).strip()
    # Remove commas
    s = s.replace(",", "")

    try:
        val = float(s)
        if val < 0:
            return None
        return round(val, 2)
    except (ValueError, TypeError):
        return None


def normalize_enum(raw: str | None, allowed_values: list[str]) -> str | None:
    """
    Compare case-insensitively against values and return stored lower-case.
    Returns lower-case string or None if not matching allowed values.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None

    for val in allowed_values:
        if s == val.lower():
            return val.lower()
    return None


def parse_timestamp(raw: str | None) -> datetime | None:
    """
    Parse timestamp string into an aware datetime in IST (+05:30).
    Timestamps come in three forms:
    1. UTC: e.g. 2026-10-05T13:20:21Z
    2. Offset: e.g. 2026-10-05T18:50:21+05:30
    3. Naive: e.g. 2026-10-03 10:01:00 (Naive means IST per spec)
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    try:
        if s.endswith("Z"):
            # UTC timestamp
            dt_utc = datetime.fromisoformat(s[:-1] + "+00:00")
            return dt_utc.astimezone(IST)

        if "+" in s or ("-" in s and "T" in s and s.count("-") > 2):
            # Already has offset
            dt = datetime.fromisoformat(s)
            return dt.astimezone(IST)

        # Naive: could be "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS"
        s_iso = s.replace(" ", "T")
        dt_naive = datetime.fromisoformat(s_iso)
        return dt_naive.replace(tzinfo=IST)
    except (ValueError, TypeError):
        return None


def format_timestamp_ist(dt: datetime | None) -> str:
    """Format an aware datetime as ISO-8601 with +05:30 offset."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")
