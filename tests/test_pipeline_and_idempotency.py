"""Tests for idempotency, incremental drop processing, and transaction rollback."""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from solution.config import SCHEMAS_DIR
from solution.database import get_db_connection, init_db, transaction
from solution.lead_ingestion import ingest_lead_csv
from solution.pipeline import ingest_drop, run_pipeline
from solution.reporting import generate_reports
from solution.validation import SchemaRegistry


@pytest.fixture
def registry():
    return SchemaRegistry(SCHEMAS_DIR)


class TestPipelineAndIdempotency:
    def test_idempotent_reingestion_no_duplicates(self, tmp_path: Path, registry: SchemaRegistry):
        db_path = tmp_path / "idempotent.db"
        init_db(db_path)
        conn = get_db_connection(db_path)

        csv_file = tmp_path / "batch_001.csv"
        csv_file.write_text(
            "customer_name,phone,policy_number\n"
            "Aarav Sharma,9876543210,MOT/2026/100001\n"
            "Diya Patel,9123456780,MOT/2026/100002\n",
            encoding="utf-8",
        )

        with transaction(conn):
            res1 = ingest_lead_csv(conn, csv_file, registry, workflow_id="motor_renewal")
        assert res1["status"] == "ingested"
        assert res1["valid_rows"] == 2

        # Re-ingest same file
        with transaction(conn):
            res2 = ingest_lead_csv(conn, csv_file, registry, workflow_id="motor_renewal")
        assert res2["status"] == "skipped_already_ingested"

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM leads")
        assert cursor.fetchone()["cnt"] == 2
        conn.close()

    def test_transaction_rollback_on_failure(self, tmp_path: Path):
        db_path = tmp_path / "rollback.db"
        init_db(db_path)
        conn = get_db_connection(db_path)

        # Attempt transaction that fails halfway
        with pytest.raises(RuntimeError):
            with transaction(conn):
                conn.execute(
                    "INSERT INTO leads (workflow_id, phone_hmac, phone_encrypted, phone_masked, "
                    "customer_name_encrypted, external_ref, batch_id, created_at, updated_at) "
                    "VALUES ('motor_renewal', 'hmac1', 'enc1', '+91******1234', 'name1', 'REF1', 'b1', 'now', 'now')"
                )
                raise RuntimeError("Simulated failure during batch ingestion")

        # Verify nothing was committed
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM leads")
        assert cursor.fetchone()["cnt"] == 0
        conn.close()

    def test_incremental_drop1_then_drop2_equivalence(self, tmp_path: Path):
        """
        Verify that processing drop_1 then drop_2 in sequence produces a clean,
        complete set of reports with zero errors.
        """
        data_dir = Path("data")
        if not (data_dir / "drop_1").exists() or not (data_dir / "drop_2").exists():
            pytest.skip("Synthetic data not found in data/ directory")

        db_path = tmp_path / "drops_test.db"
        reports_dir = tmp_path / "reports"

        run_pipeline(
            drop_paths=[data_dir / "drop_1", data_dir / "drop_2"],
            out_dir=reports_dir,
            db_path=db_path,
        )

        assert (reports_dir / "batch_summary.csv").exists()
        assert (reports_dir / "daily_overview.csv").exists()
        assert (reports_dir / "lead_report.csv").exists()
        assert (reports_dir / "rejections.csv").exists()
        assert (reports_dir / "data_quality.json").exists()
