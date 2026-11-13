"""Unit and integration tests for JSONL event ingestion and deduplication."""
from __future__ import annotations

import json
import sqlite3
import pytest
from pathlib import Path

from solution.config import SCHEMAS_DIR
from solution.database import get_db_connection, init_db, transaction
from solution.event_ingestion import ingest_event_jsonl
from solution.lead_ingestion import ingest_lead_csv
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


class TestEventIngestion:
    def test_event_deduplication_and_malformed(
        self, test_db: sqlite3.Connection, registry: SchemaRegistry, tmp_path: Path
    ):
        events_file = tmp_path / "events_0001.jsonl"
        lines = [
            '{"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "9876543210", "payload": {}}',
            '{"event_id": "evt_1", "event_type": "call.initiated", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01T10:00:00Z", "phone": "9876543210", "payload": {}}',  # duplicate
            'not a json line',  # malformed
            '{"event_type": "call.initiated"}',  # missing event_id -> malformed
            '{"event_id": "evt_2", "event_type": "disposition.set", "workflow_id": "motor_renewal", "call_id": "c1", "occurred_at": "2026-10-01 10:05:00", "phone": "9876543210", "payload": {"disposition_code": "unknown_code_123"}}',  # unknown disposition
        ]
        events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with transaction(test_db):
            res = ingest_event_jsonl(test_db, events_file, registry)

        assert res["events_total"] == 5
        assert res["malformed_events"] == 2
        assert res["duplicate_events_dropped"] == 1
        assert res["unknown_disposition_events"] == 1
        assert res["stored_events"] == 2  # evt_1 and evt_2

        # Check that both are orphans because lead is not yet ingested
        cursor = test_db.cursor()
        cursor.execute("SELECT COUNT(*) as orphan_count FROM events WHERE is_orphan = 1")
        assert cursor.fetchone()["orphan_count"] == 2
