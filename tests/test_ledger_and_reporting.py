"""Tests for interaction ledger, orphan linking, effective disposition, and report correctness."""
from __future__ import annotations

import csv
import json
import sqlite3
import pytest
from pathlib import Path

from solution.config import SCHEMAS_DIR
from solution.database import get_db_connection, init_db, transaction
from solution.event_ingestion import ingest_event_jsonl
from solution.lead_ingestion import ingest_lead_csv
from solution.ledger import LedgerService
from solution.reporting import determine_lead_bucket, generate_reports
from solution.validation import SchemaRegistry


@pytest.fixture
def test_db(tmp_path: Path):
    db_file = tmp_path / "test.db"
    init_db(db_file)
    conn = get_db_connection(db_file)
    yield conn
    conn.close()


@pytest.fixture
def registry():
    return SchemaRegistry(SCHEMAS_DIR)


class TestLedgerAndReporting:
    def test_events_before_leads_orphan_linking(
        self, test_db: sqlite3.Connection, registry: SchemaRegistry, tmp_path: Path
    ):
        # 1. Ingest event first
        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "9876543210", "payload": {}}\n'
            '{"event_id": "evt_2", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:01:00Z", "phone": "9876543210", "payload": {}}\n'
            '{"event_id": "evt_3", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:05:00Z", "phone": "9876543210", "payload": {"disposition_code": "payment_link_sent"}}\n'
            '{"event_id": "evt_4", "event_type": "disposition.override", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T12:00:00Z", "payload": {"disposition_code": "renewed"}}\n',
            encoding="utf-8",
        )
        with transaction(test_db):
            ingest_event_jsonl(test_db, events_file, registry)

        cursor = test_db.cursor()
        cursor.execute("SELECT COUNT(*) as orphans FROM events WHERE is_orphan = 1")
        assert cursor.fetchone()["orphans"] == 4

        # 2. Ingest lead later
        leads_file = tmp_path / "batch_001.csv"
        leads_file.write_text(
            "customer_name,phone,policy_number\n"
            "Aarav Sharma,9876543210,MOT/2026/100001\n",
            encoding="utf-8",
        )
        with transaction(test_db):
            res_lead = ingest_lead_csv(test_db, leads_file, registry, workflow_id="motor_renewal")

        assert res_lead["reconciled_events"] == 4

        cursor.execute("SELECT COUNT(*) as orphans FROM events WHERE is_orphan = 1")
        assert cursor.fetchone()["orphans"] == 0

        # Check effective disposition: override "renewed" over "payment_link_sent"
        ledger_svc = LedgerService(test_db, registry)
        calls = ledger_svc.get_all_valid_calls()
        assert "c1" in calls
        c1 = calls["c1"]
        assert c1.effective_disposition == "renewed"
        assert c1.is_terminal is True
        assert c1.is_success is True

    def test_bucket_precedence_rules(self):
        # 1. No calls -> yet_to_reach_out
        assert determine_lead_bucket(total_calls=0, has_terminal=False, total_telephony_failed_calls=0) == "yet_to_reach_out"

        # 2. Terminal exists -> completed (takes precedence over telephony failed)
        assert determine_lead_bucket(total_calls=2, has_terminal=True, total_telephony_failed_calls=2) == "completed"

        # 3. All telephony failed -> in_progress_telephony_failed
        assert determine_lead_bucket(total_calls=3, has_terminal=False, total_telephony_failed_calls=3) == "in_progress_telephony_failed"

        # 4. Dialed but not all telephony failed and not terminal -> in_progress_dialed
        assert determine_lead_bucket(total_calls=3, has_terminal=False, total_telephony_failed_calls=2) == "in_progress_dialed"

    def test_reports_generation_content(
        self, test_db: sqlite3.Connection, registry: SchemaRegistry, tmp_path: Path
    ):
        # Setup lead and call
        leads_file = tmp_path / "batch_001.csv"
        leads_file.write_text(
            "customer_name,phone,policy_number\n"
            "Aarav Sharma,9876543210,MOT/2026/100001\n",
            encoding="utf-8",
        )
        with transaction(test_db):
            ingest_lead_csv(test_db, leads_file, registry, workflow_id="motor_renewal")

        events_file = tmp_path / "events.jsonl"
        events_file.write_text(
            '{"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "9876543210", "payload": {}}\n'
            '{"event_id": "evt_2", "event_type": "call.connected", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:01:00Z", "phone": "9876543210", "payload": {}}\n'
            '{"event_id": "evt_3", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:05:00Z", "phone": "9876543210", "payload": {"disposition_code": "renewed"}}\n',
            encoding="utf-8",
        )
        with transaction(test_db):
            ingest_event_jsonl(test_db, events_file, registry)

        out_reports = tmp_path / "reports"
        generate_reports(test_db, registry, out_reports)

        # 1. batch_summary.csv
        with open(out_reports / "batch_summary.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) == 1
            assert reader[0]["workflow_id"] == "motor_renewal"
            assert reader[0]["batch_id"] == "batch_001"
            assert reader[0]["total_leads"] == "1"
            assert reader[0]["completed"] == "1"

        # 2. daily_overview.csv
        with open(out_reports / "daily_overview.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) == 1
            assert reader[0]["date"] == "2026-10-01"
            assert reader[0]["total_calls"] == "1"
            assert reader[0]["connected_calls"] == "1"
            assert reader[0]["connectivity_rate"] == "1.0000"
            assert reader[0]["success_leads"] == "1"

        # 3. lead_report.csv
        with open(out_reports / "lead_report.csv", "r", encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
            assert len(reader) == 1
            assert reader[0]["external_ref"] == "MOT/2026/100001"
            assert reader[0]["phone_masked"] == "+91******3210"
            assert reader[0]["bucket"] == "completed"
            assert reader[0]["total_calls"] == "1"
            assert reader[0]["connected_calls"] == "1"
            assert reader[0]["current_disposition"] == "renewed"
            assert reader[0]["ever_success"] == "true"

        # 5. data_quality.json
        with open(out_reports / "data_quality.json", "r", encoding="utf-8") as f:
            dq = json.load(f)
            assert dq["lead_rows_total"] == 1
            assert dq["lead_rows_rejected"] == 0
            assert dq["events_total"] == 3
            assert dq["orphan_events"] == 0
