"""Unit tests for schema validation and rejection rules."""
from __future__ import annotations

import pytest

from solution.config import SCHEMAS_DIR
from solution.validation import SchemaRegistry, build_header_mapping, validate_lead_row


@pytest.fixture
def registry():
    return SchemaRegistry(SCHEMAS_DIR)


class TestValidationAndRejections:
    def test_schema_loading(self, registry: SchemaRegistry):
        motor_schema = registry.get_schema("motor_renewal")
        assert motor_schema is not None
        assert motor_schema["workflow_id"] == "motor_renewal"

        feedback_schema = registry.get_schema("policy_feedback")
        assert feedback_schema is not None

        # Check disposition lookup
        disp = registry.get_disposition("motor_renewal", "renewed")
        assert disp is not None
        assert disp.is_terminal is True
        assert disp.is_success is True

    def test_alias_header_mapping(self, registry: SchemaRegistry):
        motor_schema = registry.get_schema("motor_renewal")
        fields = motor_schema["fields"]

        raw_headers = ["Language", "Insured Name", "Policy No", "Mobile No", "Expiry Date", "Premium", "agent_notes"]
        mapping = build_header_mapping(raw_headers, fields)

        assert mapping["Insured Name"]["name"] == "customer_name"
        assert mapping["Mobile No"]["name"] == "phone"
        assert mapping["Policy No"]["name"] == "policy_number"
        assert mapping["Expiry Date"]["name"] == "policy_expiry"
        assert mapping["Premium"]["name"] == "premium_amount"
        assert mapping["Language"]["name"] == "preferred_language"
        assert "agent_notes" not in mapping

    def test_rejection_order_missing_name_first(self, registry: SchemaRegistry):
        motor_schema = registry.get_schema("motor_renewal")
        fields = motor_schema["fields"]
        mapping = {f["name"]: f for f in fields}

        # Row with missing name AND bad phone
        # In schema order: customer_name is checked first!
        raw_row = {
            "customer_name": "  ",
            "phone": "bad_phone_5123",
            "policy_number": "MOT/2026/100001",
        }
        res = validate_lead_row(raw_row, mapping, fields)
        assert res.is_valid is False
        assert res.reason == "missing_required:customer_name"

    def test_rejection_invalid_phone(self, registry: SchemaRegistry):
        motor_schema = registry.get_schema("motor_renewal")
        fields = motor_schema["fields"]
        mapping = {f["name"]: f for f in fields}

        # Customer name present, phone invalid
        raw_row = {
            "customer_name": "Aarav Sharma",
            "phone": "5123456789",  # bad prefix 5
            "policy_number": "MOT/2026/100001",
        }
        res = validate_lead_row(raw_row, mapping, fields)
        assert res.is_valid is False
        assert res.reason == "invalid_phone"

    def test_rejection_missing_phone(self, registry: SchemaRegistry):
        motor_schema = registry.get_schema("motor_renewal")
        fields = motor_schema["fields"]
        mapping = {f["name"]: f for f in fields}

        raw_row = {
            "customer_name": "Aarav Sharma",
            "phone": "",
            "policy_number": "MOT/2026/100001",
        }
        res = validate_lead_row(raw_row, mapping, fields)
        assert res.is_valid is False
        assert res.reason == "missing_required:phone"

    def test_optional_invalid_generates_warning_not_rejection(self, registry: SchemaRegistry):
        motor_schema = registry.get_schema("motor_renewal")
        fields = motor_schema["fields"]
        mapping = {f["name"]: f for f in fields}

        # Valid name, phone, policy_number, but invalid expiry date and invalid premium
        raw_row = {
            "customer_name": "Aarav Sharma",
            "phone": "+91 98765 43210",
            "policy_number": "MOT/2026/100001",
            "policy_expiry": "31/02/2026",  # invalid date
            "premium_amount": "N/A",  # invalid decimal
            "preferred_language": "Hindi",  # invalid enum
        }
        res = validate_lead_row(raw_row, mapping, fields)
        assert res.is_valid is True
        assert res.reason is None
        assert res.field_warnings == 3
        assert res.cleaned_fields["policy_expiry"] is None
        assert res.cleaned_fields["premium_amount"] is None
        assert res.cleaned_fields["preferred_language"] is None
