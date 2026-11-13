"""Event ingestion: JSONL streaming, deduplication, lead association, orphan quarantining, and disposition validation."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from solution.config import IST
from solution.normalization import format_timestamp_ist, normalize_phone, parse_timestamp
from solution.pii import compute_phone_hmac, encrypt_pii
from solution.validation import SchemaRegistry


def compute_file_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file for idempotency checking."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def ingest_event_jsonl(
    conn: sqlite3.Connection,
    file_path: Path,
    registry: SchemaRegistry,
    drop_name: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Ingest a single event JSONL file into the append-only ledger.
    - Idempotent: skips if file already ingested with same hash unless force=True.
    - Non-empty lines count toward events_total.
    - Step 1: Malformed JSON or missing event_id -> count and skip.
    - Step 2: Duplicate event_id -> count and skip.
    - Step 3: Link to lead (by workflow_id + phone_hmac, or call_id for overrides).
             If unlinked, quarantine as is_orphan=1.
    - Step 4: Unknown disposition -> count and mark as is_unknown_disposition=1.
    - Normalizes timestamps to IST.
    - Protects PII (phone is stored encrypted + hashed, never plaintext).
    """
    file_path = Path(file_path).resolve()
    file_hash = compute_file_hash(file_path)

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT file_hash, total_lines, malformed_count, duplicate_count, unknown_disp_count
        FROM ingested_files WHERE file_path = ?
        """,
        (str(file_path),),
    )
    row = cursor.fetchone()
    if row and not force:
        if row["file_hash"] == file_hash:
            return {
                "file_path": str(file_path),
                "status": "skipped_already_ingested",
                "events_total": row["total_lines"],
                "malformed_events": row["malformed_count"],
                "duplicate_events_dropped": row["duplicate_count"],
                "unknown_disposition_events": row["unknown_disp_count"],
            }

    now_iso = datetime.now(timezone.utc).astimezone(IST).isoformat()

    events_total = 0
    malformed_events = 0
    duplicate_events_dropped = 0
    unknown_disposition_events = 0
    stored_events_count = 0

    # Cache known event_ids in this process run to avoid repeated queries
    seen_event_ids: set[str] = set()

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped_line = line.strip()
            if not stripped_line:
                # Empty lines do not count toward events_total
                continue

            events_total += 1

            # 1. Malformed check
            try:
                event_data = json.loads(stripped_line)
                if not isinstance(event_data, dict):
                    malformed_events += 1
                    continue
                event_id = event_data.get("event_id")
                if not event_id or not isinstance(event_id, str):
                    malformed_events += 1
                    continue
            except Exception:
                malformed_events += 1
                continue

            # 2. Duplicate check
            if event_id in seen_event_ids:
                duplicate_events_dropped += 1
                continue

            cursor.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,))
            if cursor.fetchone() is not None:
                seen_event_ids.add(event_id)
                duplicate_events_dropped += 1
                continue

            seen_event_ids.add(event_id)

            # Extract fields
            event_type = event_data.get("event_type", "")
            workflow_id = event_data.get("workflow_id", "")
            call_id = event_data.get("call_id", "")
            occurred_at_raw = event_data.get("occurred_at", "")
            raw_phone = event_data.get("phone")
            payload = event_data.get("payload") or {}
            payload_json = json.dumps(payload, ensure_ascii=False)

            # Parse timestamp to IST
            parsed_dt = parse_timestamp(occurred_at_raw)
            occurred_at_ist = format_timestamp_ist(parsed_dt) if parsed_dt else now_iso

            # Phone processing
            canonical_phone = normalize_phone(raw_phone) if raw_phone else None
            phone_hmac = compute_phone_hmac(canonical_phone) if canonical_phone else None
            phone_encrypted = encrypt_pii(canonical_phone or (str(raw_phone) if raw_phone else None))

            # 3. Lead association (Orphan check)
            lead_id: int | None = None
            is_orphan = True

            if phone_hmac:
                cursor.execute(
                    "SELECT id FROM leads WHERE workflow_id = ? AND phone_hmac = ?",
                    (workflow_id, phone_hmac),
                )
                lead_row = cursor.fetchone()
                if lead_row:
                    lead_id = lead_row["id"]
                    is_orphan = False

            # If still unlinked, try linking via call_id (e.g. for overrides or calls where phone was linked)
            if lead_id is None and call_id:
                cursor.execute(
                    "SELECT lead_id FROM events WHERE call_id = ? AND lead_id IS NOT NULL LIMIT 1",
                    (call_id,),
                )
                call_lead_row = cursor.fetchone()
                if call_lead_row:
                    lead_id = call_lead_row["lead_id"]
                    is_orphan = False

            # 4. Unknown disposition check
            disp_code: str | None = None
            is_unknown_disp = False
            if event_type in ("disposition.set", "disposition.override"):
                disp_code = payload.get("disposition_code")
                if not disp_code or not registry.is_known_disposition(workflow_id, disp_code):
                    unknown_disposition_events += 1
                    is_unknown_disp = True

            # Insert into append-only interaction ledger
            cursor.execute(
                """
                INSERT INTO events (
                    event_id, event_type, workflow_id, call_id, occurred_at,
                    phone_hmac, phone_encrypted, lead_id, payload_json,
                    disposition_code, is_orphan, is_unknown_disposition, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    workflow_id,
                    call_id,
                    occurred_at_ist,
                    phone_hmac,
                    phone_encrypted,
                    lead_id,
                    payload_json,
                    disp_code,
                    1 if is_orphan else 0,
                    1 if is_unknown_disp else 0,
                    now_iso,
                ),
            )
            stored_events_count += 1

            # If this event resolved a lead_id, link other unlinked events for the same call_id
            if lead_id is not None and call_id:
                cursor.execute(
                    """
                    UPDATE events
                    SET lead_id = ?, is_orphan = 0
                    WHERE call_id = ? AND lead_id IS NULL
                    """,
                    (lead_id, call_id),
                )

    # Record ingested file
    cursor.execute(
        """
        INSERT OR REPLACE INTO ingested_files (
            file_path, file_type, file_hash, drop_name, ingested_at,
            total_lines, processed_count, rejected_count, warning_count,
            duplicate_count, malformed_count, unknown_disp_count
        ) VALUES (?, 'event', ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
        """,
        (
            str(file_path),
            file_hash,
            drop_name or "",
            now_iso,
            events_total,
            stored_events_count,
            duplicate_events_dropped,
            malformed_events,
            unknown_disposition_events,
        ),
    )

    return {
        "file_path": str(file_path),
        "status": "ingested",
        "events_total": events_total,
        "malformed_events": malformed_events,
        "duplicate_events_dropped": duplicate_events_dropped,
        "unknown_disposition_events": unknown_disposition_events,
        "stored_events": stored_events_count,
    }
