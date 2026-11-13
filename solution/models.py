"""Data models representing leads, events, rejections, and data quality stats."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class LeadRecord:
    id: int | None
    workflow_id: str
    phone_hmac: str
    phone_encrypted: str
    phone_masked: str
    customer_name_encrypted: str
    external_ref: str
    batch_id: str
    policy_expiry: str | None = None
    premium_amount: float | None = None
    product: str | None = None
    order_date: str | None = None
    preferred_language: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RejectionRecord:
    workflow_id: str
    batch_id: str
    row_number: int
    reason: str


@dataclass
class EventRecord:
    event_id: str
    event_type: str
    workflow_id: str
    call_id: str
    occurred_at: str  # ISO IST timestamp
    payload_json: str
    phone_hmac: str | None = None
    phone_encrypted: str | None = None
    lead_id: int | None = None
    disposition_code: str | None = None
    is_orphan: bool = False
    is_unknown_disposition: bool = False
    created_at: str = ""


@dataclass
class DataQualityMetrics:
    lead_rows_total: int = 0
    lead_rows_rejected: int = 0
    rejections_by_reason: dict[str, int] = field(default_factory=dict)
    duplicate_lead_rows_merged: int = 0
    field_warnings: int = 0
    events_total: int = 0
    malformed_events: int = 0
    duplicate_events_dropped: int = 0
    orphan_events: int = 0
    unknown_disposition_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "lead_rows_total": self.lead_rows_total,
            "lead_rows_rejected": self.lead_rows_rejected,
            "rejections_by_reason": dict(sorted(self.rejections_by_reason.items())),
            "duplicate_lead_rows_merged": self.duplicate_lead_rows_merged,
            "field_warnings": self.field_warnings,
            "events_total": self.events_total,
            "malformed_events": self.malformed_events,
            "duplicate_events_dropped": self.duplicate_events_dropped,
            "orphan_events": self.orphan_events,
            "unknown_disposition_events": self.unknown_disposition_events,
        }
