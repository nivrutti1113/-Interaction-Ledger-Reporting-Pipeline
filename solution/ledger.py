"""Append-only interaction ledger access and derived facts calculation."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from solution.validation import SchemaRegistry


@dataclass
class CallSummary:
    call_id: str
    workflow_id: str
    lead_id: int
    initiated_at: str  # ISO IST timestamp
    call_date: str     # YYYY-MM-DD (IST)
    is_connected: bool
    is_telephony_failed: bool
    effective_disposition: str | None
    is_terminal: bool
    is_success: bool


class LedgerService:
    def __init__(self, conn: sqlite3.Connection, registry: SchemaRegistry):
        self.conn = conn
        self.registry = registry

    def get_all_valid_calls(self) -> dict[str, CallSummary]:
        """
        Derive all valid calls from the append-only ledger.
        A call is counted ONLY if its call.initiated event is present and linked (lead_id is not null).
        Effective disposition:
        - Latest disposition.override by occurred_at (known codes only).
        - If none, latest disposition.set by occurred_at (known codes only).
        - Otherwise none.
        Returns dict of call_id -> CallSummary.
        """
        cursor = self.conn.cursor()

        # Step 1: Find all calls with a linked call.initiated event
        cursor.execute(
            """
            SELECT call_id, workflow_id, lead_id, MIN(occurred_at) as initiated_at
            FROM events
            WHERE event_type = 'call.initiated'
              AND lead_id IS NOT NULL
              AND is_orphan = 0
            GROUP BY call_id, workflow_id, lead_id
            """
        )
        initiated_rows = cursor.fetchall()

        if not initiated_rows:
            return {}

        calls: dict[str, dict[str, Any]] = {}
        for r in initiated_rows:
            cid = r["call_id"]
            init_at = r["initiated_at"]
            calls[cid] = {
                "call_id": cid,
                "workflow_id": r["workflow_id"],
                "lead_id": r["lead_id"],
                "initiated_at": init_at,
                "call_date": init_at[:10],
                "is_connected": False,
                "is_telephony_failed": False,
                "overrides": [],
                "sets": [],
            }

        # Step 2: Query other events for these calls
        # Connected
        cursor.execute(
            """
            SELECT DISTINCT call_id
            FROM events
            WHERE event_type = 'call.connected'
              AND lead_id IS NOT NULL
              AND is_orphan = 0
            """
        )
        for r in cursor.fetchall():
            cid = r["call_id"]
            if cid in calls:
                calls[cid]["is_connected"] = True

        # Telephony failed
        cursor.execute(
            """
            SELECT DISTINCT call_id
            FROM events
            WHERE event_type = 'call.telephony_failed'
              AND lead_id IS NOT NULL
              AND is_orphan = 0
            """
        )
        for r in cursor.fetchall():
            cid = r["call_id"]
            if cid in calls:
                calls[cid]["is_telephony_failed"] = True

        # Dispositions (known codes only)
        cursor.execute(
            """
            SELECT call_id, event_type, disposition_code, occurred_at
            FROM events
            WHERE event_type IN ('disposition.set', 'disposition.override')
              AND lead_id IS NOT NULL
              AND is_orphan = 0
              AND is_unknown_disposition = 0
              AND disposition_code IS NOT NULL
            ORDER BY occurred_at ASC
            """
        )
        for r in cursor.fetchall():
            cid = r["call_id"]
            if cid in calls:
                etype = r["event_type"]
                code = r["disposition_code"]
                when = r["occurred_at"]
                if etype == "disposition.override":
                    calls[cid]["overrides"].append((when, code))
                else:
                    calls[cid]["sets"].append((when, code))

        # Build CallSummary objects
        result: dict[str, CallSummary] = {}
        for cid, cdata in calls.items():
            wf = cdata["workflow_id"]
            eff_disp: str | None = None
            if cdata["overrides"]:
                # Latest override by occurred_at
                eff_disp = cdata["overrides"][-1][1]
            elif cdata["sets"]:
                # Latest set by occurred_at
                eff_disp = cdata["sets"][-1][1]

            is_term = False
            is_succ = False
            if eff_disp:
                disp_info = self.registry.get_disposition(wf, eff_disp)
                if disp_info:
                    is_term = disp_info.is_terminal
                    is_succ = disp_info.is_success

            result[cid] = CallSummary(
                call_id=cid,
                workflow_id=wf,
                lead_id=cdata["lead_id"],
                initiated_at=cdata["initiated_at"],
                call_date=cdata["call_date"],
                is_connected=cdata["is_connected"],
                is_telephony_failed=cdata["is_telephony_failed"],
                effective_disposition=eff_disp,
                is_terminal=is_term,
                is_success=is_succ,
            )

        return result
