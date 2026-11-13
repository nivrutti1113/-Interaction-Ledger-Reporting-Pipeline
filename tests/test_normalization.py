"""Unit tests for normalization: phone, dates, currency, enums, headers, and timestamps."""
from __future__ import annotations

from datetime import datetime, timezone
import pytest

from solution.config import IST
from solution.normalization import (
    format_timestamp_ist,
    normalize_enum,
    normalize_header,
    normalize_phone,
    parse_date,
    parse_decimal,
    parse_timestamp,
)


class TestPhoneNormalization:
    def test_canonical_10_digits(self):
        assert normalize_phone("9876543210") == "+919876543210"
        assert normalize_phone("6123456789") == "+916123456789"
        assert normalize_phone("7123456789") == "+917123456789"
        assert normalize_phone("8123456789") == "+918123456789"

    def test_with_country_code_91(self):
        assert normalize_phone("919876543210") == "+919876543210"
        assert normalize_phone("+91 98765 43210") == "+919876543210"
        assert normalize_phone("91-98765-43210") == "+919876543210"
        assert normalize_phone("(+91) 9876543210") == "+919876543210"

    def test_with_leading_zero(self):
        assert normalize_phone("09876543210") == "+919876543210"
        assert normalize_phone("0 9876543210") == "+919876543210"

    def test_invalid_prefixes(self):
        # Indian mobile must start with 6, 7, 8, or 9
        assert normalize_phone("5123456789") is None
        assert normalize_phone("1123456789") is None
        assert normalize_phone("+91 5123456789") is None
        assert normalize_phone("05123456789") is None

    def test_invalid_lengths(self):
        assert normalize_phone("987654321") is None  # 9 digits
        assert normalize_phone("98765432101") is None  # 11 digits not starting with 0
        assert normalize_phone("9198765432101") is None  # 13 digits

    def test_landline_and_non_mobile(self):
        assert normalize_phone("022-2876-5432") is None  # landline
        assert normalize_phone("+1 415 555 0134") is None  # US phone
        assert normalize_phone("N/A") is None
        assert normalize_phone("") is None
        assert normalize_phone(None) is None


class TestDateParsing:
    def test_valid_iso_format(self):
        assert parse_date("2026-10-15") == "2026-10-15"

    def test_valid_day_first_slash_and_dash(self):
        assert parse_date("15/10/2026") == "2026-10-15"
        assert parse_date("15-10-2026") == "2026-10-15"

    def test_valid_month_name_case_insensitive(self):
        assert parse_date("01-Dec-2026") == "2026-12-01"
        assert parse_date("01-dec-2026") == "2026-12-01"
        assert parse_date("01-DEC-2026") == "2026-12-01"
        assert parse_date("21-Nov-2026") == "2026-11-21"

    def test_impossible_dates(self):
        assert parse_date("31/02/2026") is None
        assert parse_date("2026-13-01") is None
        assert parse_date("31-Feb-2026") is None

    def test_invalid_text(self):
        assert parse_date("soon") is None
        assert parse_date("TBD") is None
        assert parse_date("") is None
        assert parse_date(None) is None


class TestDecimalParsing:
    def test_plain_numbers(self):
        assert parse_decimal("12345.67") == 12345.67
        assert parse_decimal("12345") == 12345.0
        assert parse_decimal("0") == 0.0

    def test_with_currency_prefixes(self):
        assert parse_decimal("₹27,554") == 27554.0
        assert parse_decimal("₹ 27554.50") == 27554.5
        assert parse_decimal("Rs. 4000") == 4000.0
        assert parse_decimal("Rs 4000") == 4000.0
        assert parse_decimal("INR 25301.4") == 25301.4

    def test_rounding(self):
        assert parse_decimal("123.456") == 123.46

    def test_invalid_and_negative(self):
        assert parse_decimal("-100") is None
        assert parse_decimal("twelve thousand") is None
        assert parse_decimal("N/A") is None
        assert parse_decimal("-") is None
        assert parse_decimal("") is None
        assert parse_decimal(None) is None


class TestEnumNormalization:
    def test_case_insensitivity(self):
        allowed = ["en", "hi", "ta", "kn", "te", "mr"]
        assert normalize_enum("EN", allowed) == "en"
        assert normalize_enum("Hi", allowed) == "hi"
        assert normalize_enum(" ta ", allowed) == "ta"

    def test_disallowed_values(self):
        allowed = ["en", "hi", "ta", "kn", "te", "mr"]
        assert normalize_enum("Hindi", allowed) is None
        assert normalize_enum("english", allowed) is None
        assert normalize_enum("fr", allowed) is None
        assert normalize_enum("", allowed) is None
        assert normalize_enum(None, allowed) is None


class TestHeaderNormalization:
    def test_header_normalization(self):
        assert normalize_header("Insured Name") == "insured name"
        assert normalize_header("customer_name") == "customer name"
        assert normalize_header("Mobile__No ") == "mobile no"
        assert normalize_header("  Policy   Number  ") == "policy number"


class TestTimestampParsing:
    def test_utc_timestamp(self):
        dt = parse_timestamp("2026-10-01T05:26:10Z")
        assert dt is not None
        # UTC 05:26:10 + 5:30 = 10:56:10 IST
        assert dt.tzinfo == IST
        assert dt.hour == 10
        assert dt.minute == 56

    def test_offset_timestamp(self):
        dt = parse_timestamp("2026-10-01T10:40:19+05:30")
        assert dt is not None
        assert dt.tzinfo == IST
        assert dt.hour == 10
        assert dt.minute == 40

    def test_naive_timestamp_treated_as_ist(self):
        dt = parse_timestamp("2026-10-01 09:15:55")
        assert dt is not None
        assert dt.tzinfo == IST
        assert dt.hour == 9
        assert dt.minute == 15
        assert format_timestamp_ist(dt) == "2026-10-01T09:15:55+05:30"
