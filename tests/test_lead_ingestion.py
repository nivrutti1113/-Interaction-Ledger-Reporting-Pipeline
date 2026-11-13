"""Integration tests for lead ingestion, duplicate merging, and blank row handling."""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from solution.config import SCHEMAS_DIR
from solution.database import get_db_connection, init_db, transaction
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


class TestLeadIngestion:
    def test_ingest_and_merge_duplicates(self, test_db: sqlite3.Connection, registry: SchemaRegistry, tmp_path: Path):
        csv_file1 = tmp_path / "batch_001.csv"
        csv_file1.write_text(
            "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
            "Aarav Sharma,9876543210,MOT/2026/100001,2026-10-15,10000,hi\n"
            "\n"  # blank row should be skipped
            "   ,   ,   ,   ,   ,   \n"  # whitespace row should be skipped
            "Bad Lead,,MOT/2026/100002,2026-10-15,10000,hi\n"  # missing phone -> rejected
            "Aarav Sharma,9876543210,MOT/2026/100001,,12000,ta\n",  # duplicate in same file
            encoding="utf-8",
        )

        with transaction(test_db):
            res = ingest_lead_csv(test_db, csv_file1, registry, workflow_id="motor_renewal")

        assert res["total_rows"] == 3
        assert res["valid_rows"] == 2
        assert res["rejected_rows"] == 1
        assert res["merged_duplicates"] == 1

        # Check lead in DB: batch_id should be batch_001
        cursor = test_db.cursor()
        cursor.execute("SELECT batch_id, premium_amount, preferred_language, policy_expiry FROM leads")
        lead = cursor.fetchone()
        assert lead["batch_id"] == "batch_001"
        # Overwrote premium_amount (12000) and preferred_language (ta), kept policy_expiry (2026-10-15)
        assert lead["premium_amount"] == 12000.0
        assert lead["preferred_language"] == "ta"
        assert lead["policy_expiry"] == "2026-10-15"

        # Check rejection
        cursor.execute("SELECT workflow_id, batch_id, row_number, reason FROM rejections")
        rejs = cursor.fetchall()
        assert len(rejs) == 1
        assert rejs[0]["row_number"] == 2
        assert rejs[0]["reason"] == "missing_required:phone"

    def test_duplicate_across_batches_preserves_first_batch_id(
        self, test_db: sqlite3.Connection, registry: SchemaRegistry, tmp_path: Path
    ):
        csv_file1 = tmp_path / "batch_001.csv"
        csv_file1.write_text(
            "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
            "Diya Patel,9123456780,MOT/2026/100010,2026-11-01,15000,\n",
            encoding="utf-8",
        )
        csv_file2 = tmp_path / "batch_002.csv"
        csv_file2.write_text(
            "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language\n"
            "Diya Patel,9123456780,MOT/2026/100010,,16000,mr\n",
            encoding="utf-8",
        )

        with transaction(test_db):
            ingest_lead_csv(test_db, csv_file1, registry, workflow_id="motor_renewal")

        with transaction(test_db):
            res2 = ingest_lead_csv(test_db, csv_file2, registry, workflow_id="motor_renewal")

        assert res2["merged_duplicates"] == 1
        cursor = test_db.cursor()
        cursor.execute("SELECT batch_id, premium_amount, preferred_language, policy_expiry FROM leads")
        lead = cursor.fetchone()
        # Permanent first batch_id:
        assert lead["batch_id"] == "batch_001"
        assert lead["premium_amount"] == 16000.0
        assert lead["preferred_language"] == "mr"
        assert lead["policy_expiry"] == "2026-11-01"
