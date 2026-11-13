"""Lead ingestion: CSV parsing, header mapping, validation, duplicate merging, and default application."""
from __future__ import annotations

import csv
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.config import IST
from solution.models import DataQualityMetrics, RejectionRecord
from solution.pii import compute_phone_hmac, encrypt_pii, mask_phone
from solution.validation import SchemaRegistry, build_header_mapping, validate_lead_row


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file for idempotency checking."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_blank_row(row: list[str]) -> bool:
    """Return True if every cell in the row is empty or whitespace."""
    return all(cell.strip() == "" for cell in row)


def reconcile_orphaned_events(conn: sqlite3.Connection) -> int:
    """
    Reconcile previously orphaned events against existing leads.
    1. Match unlinked events having phone_hmac with leads having same workflow_id and phone_hmac.
    2. Match unlinked events without phone (e.g. disposition.override) with leads via call_id.
    Returns number of newly linked events.
    """
    cursor = conn.cursor()

    # 1. Link by workflow_id and phone_hmac
    cursor.execute(
        """
        UPDATE events
        SET lead_id = (
            SELECT leads.id FROM leads
            WHERE leads.workflow_id = events.workflow_id
              AND leads.phone_hmac = events.phone_hmac
        ),
        is_orphan = 0
        WHERE events.lead_id IS NULL
          AND events.phone_hmac IS NOT NULL
          AND events.phone_hmac != ''
          AND EXISTS (
              SELECT 1 FROM leads
              WHERE leads.workflow_id = events.workflow_id
                AND leads.phone_hmac = events.phone_hmac
          );
        """
    )
    linked_by_phone = cursor.rowcount

    # 2. Link remaining by call_id (e.g. disposition.override)
    cursor.execute(
        """
        UPDATE events
        SET lead_id = (
            SELECT ev2.lead_id FROM events ev2
            WHERE ev2.call_id = events.call_id
              AND ev2.lead_id IS NOT NULL
            LIMIT 1
        ),
        is_orphan = 0
        WHERE events.lead_id IS NULL
          AND EXISTS (
              SELECT 1 FROM events ev2
              WHERE ev2.call_id = events.call_id
                AND ev2.lead_id IS NOT NULL
          );
        """
    )
    linked_by_call = cursor.rowcount

    return linked_by_phone + linked_by_call


def ingest_lead_csv(
    conn: sqlite3.Connection,
    file_path: Path,
    registry: SchemaRegistry,
    workflow_id: str | None = None,
    drop_name: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Ingest a single lead CSV file into the database.
    - Idempotent: skips if file already ingested with same hash unless force=True.
    - Preserves file stem as batch_id permanently for new leads.
    - Validates against workflow schema in schema order.
    - Merges duplicates (overwrites only non-null fields; first batch_id wins).
    - Applies schema defaults after merge to fields that remain null.
    - Reconciles orphaned events after lead insertion.
    """
    file_path = Path(file_path).resolve()
    file_hash = compute_file_hash(file_path)

    # Check if already ingested
    cursor = conn.cursor()
    cursor.execute(
        "SELECT file_hash, processed_count, rejected_count FROM ingested_files WHERE file_path = ?",
        (str(file_path),),
    )
    row = cursor.fetchone()
    if row and not force:
        if row["file_hash"] == file_hash:
            # Already ingested and unchanged
            return {
                "file_path": str(file_path),
                "status": "skipped_already_ingested",
                "processed": row["processed_count"],
                "rejected": row["rejected_count"],
            }

    # Infer workflow_id if not provided
    if not workflow_id:
        parent_dir = file_path.parent.name
        if registry.get_schema(parent_dir):
            workflow_id = parent_dir
        else:
            raise ValueError(f"Could not infer workflow_id for {file_path}")

    schema = registry.get_schema(workflow_id)
    if not schema:
        raise ValueError(f"Unknown workflow_id: {workflow_id}")

    schema_fields = schema.get("fields", [])
    batch_id = file_path.stem
    ref_field_name = registry.get_reference_field_name(workflow_id)

    # Identify field defaults
    field_defaults = {
        f["name"]: f["default"]
        for f in schema_fields
        if "default" in f
    }

    # Read CSV
    total_non_blank_rows = 0
    rejected_rows_count = 0
    merged_duplicates_count = 0
    field_warnings_count = 0
    rejections_to_insert: list[tuple[str, str, int, str]] = []

    now_iso = datetime.now(timezone.utc).astimezone(IST).isoformat()

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        try:
            raw_headers = next(reader)
        except StopIteration:
            # Empty file
            raw_headers = []

        header_mapping = build_header_mapping(raw_headers, schema_fields)

        # Track row number for non-blank data rows starting at 1
        row_number = 0
        for raw_cells in reader:
            if is_blank_row(raw_cells):
                # Blank rows are skipped and NOT counted
                continue

            row_number += 1
            total_non_blank_rows += 1

            # Build dict for row based on headers
            row_dict = {}
            for idx, h in enumerate(raw_headers):
                if idx < len(raw_cells):
                    row_dict[h] = raw_cells[idx]
                else:
                    row_dict[h] = ""

            val_res = validate_lead_row(row_dict, header_mapping, schema_fields)
            if not val_res.is_valid:
                rejected_rows_count += 1
                rejections_to_insert.append((workflow_id, batch_id, row_number, val_res.reason or "unknown"))
                continue

            field_warnings_count += val_res.field_warnings
            cleaned = val_res.cleaned_fields or {}

            canonical_phone = cleaned.get("phone", "")
            phone_hmac = compute_phone_hmac(canonical_phone)
            phone_masked = mask_phone(canonical_phone)
            phone_enc = encrypt_pii(canonical_phone)
            name_enc = encrypt_pii(cleaned.get("customer_name", ""))
            external_ref = cleaned.get(ref_field_name, "")

            # Check for existing lead by (workflow_id, phone_hmac)
            cursor.execute(
                """
                SELECT id, batch_id, customer_name_encrypted, external_ref,
                       policy_expiry, premium_amount, product, order_date, preferred_language
                FROM leads
                WHERE workflow_id = ? AND phone_hmac = ?
                """,
                (workflow_id, phone_hmac),
            )
            existing_lead = cursor.fetchone()

            if existing_lead is None:
                # First valid row creates the lead with permanent batch_id
                # Apply defaults to fields that are null
                lead_data: dict[str, Any] = dict(cleaned)
                for df_key, df_val in field_defaults.items():
                    if lead_data.get(df_key) is None:
                        lead_data[df_key] = df_val

                cursor.execute(
                    """
                    INSERT INTO leads (
                        workflow_id, phone_hmac, phone_encrypted, phone_masked,
                        customer_name_encrypted, external_ref, batch_id,
                        policy_expiry, premium_amount, product, order_date,
                        preferred_language, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        phone_hmac,
                        phone_enc,
                        phone_masked,
                        name_enc,
                        str(external_ref),
                        batch_id,
                        lead_data.get("policy_expiry"),
                        lead_data.get("premium_amount"),
                        lead_data.get("product"),
                        lead_data.get("order_date"),
                        lead_data.get("preferred_language"),
                        now_iso,
                        now_iso,
                    ),
                )
            else:
                # Merged duplicate
                merged_duplicates_count += 1
                lead_id = existing_lead["id"]

                # Overwrite only fields where new value is non-null
                new_name_enc = name_enc if cleaned.get("customer_name") else existing_lead["customer_name_encrypted"]
                new_ref = external_ref if cleaned.get(ref_field_name) else existing_lead["external_ref"]
                new_expiry = cleaned.get("policy_expiry") if cleaned.get("policy_expiry") is not None else existing_lead["policy_expiry"]
                new_premium = cleaned.get("premium_amount") if cleaned.get("premium_amount") is not None else existing_lead["premium_amount"]
                new_product = cleaned.get("product") if cleaned.get("product") is not None else existing_lead["product"]
                new_order_date = cleaned.get("order_date") if cleaned.get("order_date") is not None else existing_lead["order_date"]
                new_lang = cleaned.get("preferred_language") if cleaned.get("preferred_language") is not None else existing_lead["preferred_language"]

                # Apply default if still null
                if new_lang is None and "preferred_language" in field_defaults:
                    new_lang = field_defaults["preferred_language"]

                cursor.execute(
                    """
                    UPDATE leads
                    SET customer_name_encrypted = ?,
                        external_ref = ?,
                        policy_expiry = ?,
                        premium_amount = ?,
                        product = ?,
                        order_date = ?,
                        preferred_language = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        new_name_enc,
                        str(new_ref),
                        new_expiry,
                        new_premium,
                        new_product,
                        new_order_date,
                        new_lang,
                        now_iso,
                        lead_id,
                    ),
                )

    # Insert rejections
    if rejections_to_insert:
        cursor.executemany(
            """
            INSERT OR REPLACE INTO rejections (workflow_id, batch_id, row_number, reason)
            VALUES (?, ?, ?, ?)
            """,
            rejections_to_insert,
        )

    # Reconcile previously orphaned events
    reconciled = reconcile_orphaned_events(conn)

    # Record ingested file
    cursor.execute(
        """
        INSERT OR REPLACE INTO ingested_files (
            file_path, file_type, file_hash, drop_name, ingested_at,
            total_lines, processed_count, rejected_count, warning_count,
            duplicate_count
        ) VALUES (?, 'lead', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(file_path),
            file_hash,
            drop_name or "",
            now_iso,
            total_non_blank_rows,
            total_non_blank_rows - rejected_rows_count,
            rejected_rows_count,
            field_warnings_count,
            merged_duplicates_count,
        ),
    )

    return {
        "file_path": str(file_path),
        "status": "ingested",
        "total_rows": total_non_blank_rows,
        "valid_rows": total_non_blank_rows - rejected_rows_count,
        "rejected_rows": rejected_rows_count,
        "merged_duplicates": merged_duplicates_count,
        "field_warnings": field_warnings_count,
        "reconciled_events": reconciled,
    }
