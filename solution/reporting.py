"""Report generation for all five required outputs."""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from solution.ledger import LedgerService
from solution.models import DataQualityMetrics
from solution.validation import SchemaRegistry


def determine_lead_bucket(
    total_calls: int,
    has_terminal: bool,
    total_telephony_failed_calls: int,
) -> str:
    """
    Determine lead bucket in strict precedence order:
    1. yet_to_reach_out: no calls.
    2. completed: any call's effective disposition is terminal (is_terminal=true).
    3. in_progress_telephony_failed: every call has a call.telephony_failed event.
    4. in_progress_dialed: everything else.
    """
    if total_calls == 0:
        return "yet_to_reach_out"
    if has_terminal:
        return "completed"
    if total_telephony_failed_calls == total_calls:
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def generate_reports(
    conn: sqlite3.Connection,
    registry: SchemaRegistry,
    out_dir: Path | str,
) -> dict[str, Path]:
    """
    Generate all 5 contract reports into out_dir:
    1. batch_summary.csv
    2. daily_overview.csv
    3. lead_report.csv
    4. rejections.csv
    5. data_quality.json
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    ledger_svc = LedgerService(conn, registry)
    calls_by_id = ledger_svc.get_all_valid_calls()

    # Index calls by lead_id
    calls_by_lead: dict[int, list[Any]] = defaultdict(list)
    for c in calls_by_id.values():
        calls_by_lead[c.lead_id].append(c)

    cursor = conn.cursor()

    # ==========================================
    # 1. Lead processing for batch & lead reports
    # ==========================================
    cursor.execute(
        """
        SELECT id, workflow_id, external_ref, batch_id, phone_masked
        FROM leads
        ORDER BY workflow_id ASC, external_ref ASC
        """
    )
    all_leads = cursor.fetchall()

    lead_report_rows: list[dict[str, Any]] = []
    # (workflow_id, batch_id) -> {bucket: count}
    batch_bucket_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "total_leads": 0,
            "yet_to_reach_out": 0,
            "in_progress_telephony_failed": 0,
            "in_progress_dialed": 0,
            "completed": 0,
        }
    )

    for lead in all_leads:
        lid = lead["id"]
        wf = lead["workflow_id"]
        bid = lead["batch_id"]
        ext_ref = lead["external_ref"]
        phone_m = lead["phone_masked"]

        lead_calls = calls_by_lead.get(lid, [])
        total_calls = len(lead_calls)
        connected_calls = sum(1 for c in lead_calls if c.is_connected)
        telephony_failed_calls = sum(1 for c in lead_calls if c.is_telephony_failed)
        has_terminal = any(c.is_terminal for c in lead_calls)
        ever_success = any(c.is_success for c in lead_calls)

        bucket = determine_lead_bucket(total_calls, has_terminal, telephony_failed_calls)

        # Batch summary accumulation
        batch_stats = batch_bucket_counts[(wf, bid)]
        batch_stats["total_leads"] += 1
        batch_stats[bucket] += 1

        # Lead report details
        last_call_at = ""
        current_disp = ""
        if lead_calls:
            # Sort calls by initiated_at ascending
            lead_calls.sort(key=lambda c: (c.initiated_at, c.call_id))
            last_call_at = lead_calls[-1].initiated_at

            # current_disposition: effective disposition of most recent call (by initiated time) that has one
            for c in reversed(lead_calls):
                if c.effective_disposition:
                    current_disp = c.effective_disposition
                    break

        lead_report_rows.append({
            "workflow_id": wf,
            "external_ref": ext_ref,
            "batch_id": bid,
            "phone_masked": phone_m,
            "bucket": bucket,
            "total_calls": total_calls,
            "connected_calls": connected_calls,
            "last_call_at": last_call_at,
            "current_disposition": current_disp,
            "ever_success": "true" if ever_success else "false",
        })

    # Write 1. batch_summary.csv
    batch_summary_path = out_path / "batch_summary.csv"
    batch_summary_rows = []
    for (wf, bid), counts in sorted(batch_bucket_counts.items(), key=lambda x: (x[0][0], x[0][1])):
        batch_summary_rows.append({
            "workflow_id": wf,
            "batch_id": bid,
            "total_leads": counts["total_leads"],
            "yet_to_reach_out": counts["yet_to_reach_out"],
            "in_progress_telephony_failed": counts["in_progress_telephony_failed"],
            "in_progress_dialed": counts["in_progress_dialed"],
            "completed": counts["completed"],
        })

    with open(batch_summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "workflow_id",
                "batch_id",
                "total_leads",
                "yet_to_reach_out",
                "in_progress_telephony_failed",
                "in_progress_dialed",
                "completed",
            ],
        )
        writer.writeheader()
        writer.writerows(batch_summary_rows)

    # Write 3. lead_report.csv (sorted by workflow_id, external_ref)
    lead_report_path = out_path / "lead_report.csv"
    with open(lead_report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "workflow_id",
                "external_ref",
                "batch_id",
                "phone_masked",
                "bucket",
                "total_calls",
                "connected_calls",
                "last_call_at",
                "current_disposition",
                "ever_success",
            ],
        )
        writer.writeheader()
        writer.writerows(lead_report_rows)

    # ==========================================
    # 2. Daily Overview Report
    # ==========================================
    # Group calls by (workflow_id, date)
    daily_groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for c in calls_by_id.values():
        daily_groups[(c.workflow_id, c.call_date)].append(c)

    daily_rows: list[dict[str, Any]] = []
    for (wf, cdate), d_calls in sorted(daily_groups.items(), key=lambda x: (x[0][0], x[0][1])):
        tot_c = len(d_calls)
        if tot_c == 0:
            continue
        conn_c = sum(1 for c in d_calls if c.is_connected)
        conn_rate = f"{conn_c / tot_c:.4f}"
        succ_leads = len({c.lead_id for c in d_calls if c.is_success})

        daily_rows.append({
            "workflow_id": wf,
            "date": cdate,
            "total_calls": tot_c,
            "connected_calls": conn_c,
            "connectivity_rate": conn_rate,
            "success_leads": succ_leads,
        })

    daily_overview_path = out_path / "daily_overview.csv"
    with open(daily_overview_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "workflow_id",
                "date",
                "total_calls",
                "connected_calls",
                "connectivity_rate",
                "success_leads",
            ],
        )
        writer.writeheader()
        writer.writerows(daily_rows)

    # ==========================================
    # 4. Rejections Report
    # ==========================================
    cursor.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM rejections
        ORDER BY workflow_id ASC, batch_id ASC, row_number ASC
        """
    )
    rej_rows = [dict(r) for r in cursor.fetchall()]

    rejections_path = out_path / "rejections.csv"
    with open(rejections_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["workflow_id", "batch_id", "row_number", "reason"],
        )
        writer.writeheader()
        writer.writerows(rej_rows)

    # ==========================================
    # 5. Data Quality JSON
    # ==========================================
    # Aggregate from ingested_files and current state
    cursor.execute(
        """
        SELECT
            COALESCE(SUM(total_lines), 0) as lead_rows_total,
            COALESCE(SUM(duplicate_count), 0) as duplicate_lead_rows_merged,
            COALESCE(SUM(warning_count), 0) as field_warnings
        FROM ingested_files
        WHERE file_type = 'lead'
        """
    )
    lead_stats = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(total_lines), 0) as events_total,
            COALESCE(SUM(malformed_count), 0) as malformed_events,
            COALESCE(SUM(duplicate_count), 0) as duplicate_events_dropped,
            COALESCE(SUM(unknown_disp_count), 0) as unknown_disposition_events
        FROM ingested_files
        WHERE file_type = 'event'
        """
    )
    event_stats = cursor.fetchone()

    # Rejection counts
    cursor.execute("SELECT COUNT(*) as total_rejected FROM rejections")
    total_rejected = cursor.fetchone()["total_rejected"]

    cursor.execute(
        """
        SELECT reason, COUNT(*) as cnt
        FROM rejections
        GROUP BY reason
        ORDER BY reason ASC
        """
    )
    rejections_by_reason = {r["reason"]: r["cnt"] for r in cursor.fetchall()}

    # Orphan events: final state of unlinked events in database
    cursor.execute("SELECT COUNT(*) as orphan_count FROM events WHERE lead_id IS NULL")
    orphan_events = cursor.fetchone()["orphan_count"]

    dq_metrics = {
        "lead_rows_total": lead_stats["lead_rows_total"],
        "lead_rows_rejected": total_rejected,
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": lead_stats["duplicate_lead_rows_merged"],
        "field_warnings": lead_stats["field_warnings"],
        "events_total": event_stats["events_total"],
        "malformed_events": event_stats["malformed_events"],
        "duplicate_events_dropped": event_stats["duplicate_events_dropped"],
        "orphan_events": orphan_events,
        "unknown_disposition_events": event_stats["unknown_disposition_events"],
    }

    data_quality_path = out_path / "data_quality.json"
    with open(data_quality_path, "w", encoding="utf-8") as f:
        json.dump(dq_metrics, f, indent=2)

    return {
        "batch_summary": batch_summary_path,
        "daily_overview": daily_overview_path,
        "lead_report": lead_report_path,
        "rejections": rejections_path,
        "data_quality": data_quality_path,
    }
