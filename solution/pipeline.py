"""Pipeline orchestration: discovering and ingesting drops, leads, and events."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from solution.database import get_db_connection, init_db, transaction
from solution.event_ingestion import ingest_event_jsonl
from solution.lead_ingestion import ingest_lead_csv, reconcile_orphaned_events
from solution.reporting import generate_reports
from solution.validation import SchemaRegistry


def ingest_drop(
    conn: sqlite3.Connection,
    registry: SchemaRegistry,
    drop_dir: Path | str,
    force: bool = False,
) -> dict[str, Any]:
    """
    Ingest a single drop folder (e.g. data/drop_1).
    - Sorts files in filename order.
    - Ingests leads first, then events.
    - Reconciles orphaned events.
    """
    drop_path = Path(drop_dir).resolve()
    drop_name = drop_path.name

    lead_results = []
    event_results = []

    # 1. Discover and ingest lead files
    # Leads are under <drop>/leads/<workflow_id>/*.csv
    leads_dir = drop_path / "leads"
    if leads_dir.exists():
        lead_files: list[Path] = []
        for wf_dir in leads_dir.iterdir():
            if wf_dir.is_dir():
                for f in wf_dir.glob("*.csv"):
                    lead_files.append(f)
        # Sort files in filename order
        lead_files.sort(key=lambda p: (p.parent.name, p.name))

        for l_file in lead_files:
            wf_id = l_file.parent.name
            with transaction(conn):
                res = ingest_lead_csv(
                    conn,
                    l_file,
                    registry,
                    workflow_id=wf_id,
                    drop_name=drop_name,
                    force=force,
                )
                lead_results.append(res)

    # 2. Discover and ingest event files
    # Events are under <drop>/events/*.jsonl
    events_dir = drop_path / "events"
    if events_dir.exists():
        event_files = sorted(events_dir.glob("*.jsonl"), key=lambda p: p.name)
        for e_file in event_files:
            with transaction(conn):
                res = ingest_event_jsonl(
                    conn,
                    e_file,
                    registry,
                    drop_name=drop_name,
                    force=force,
                )
                event_results.append(res)

    # Reconcile any remaining unlinked events across drops
    with transaction(conn):
        reconciled = reconcile_orphaned_events(conn)

    return {
        "drop": drop_name,
        "lead_files": lead_results,
        "event_files": event_results,
        "reconciled_events": reconciled,
    }


def ingest_arbitrary_path(
    conn: sqlite3.Connection,
    registry: SchemaRegistry,
    target_path: Path | str,
    force: bool = False,
) -> dict[str, Any]:
    """Ingest either a drop directory, a directory containing drops, a lead CSV, or an event JSONL."""
    p = Path(target_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Path does not exist: {p}")

    if p.is_file():
        if p.suffix.lower() == ".csv":
            with transaction(conn):
                return ingest_lead_csv(conn, p, registry, force=force)
        elif p.suffix.lower() in (".jsonl", ".json"):
            with transaction(conn):
                return ingest_event_jsonl(conn, p, registry, force=force)
        else:
            raise ValueError(f"Unsupported file type: {p.suffix}")

    # If directory: check if it contains drops (drop_1, drop_2) or is a drop itself
    subdrops = [d for d in p.iterdir() if d.is_dir() and d.name.startswith("drop_")]
    if subdrops:
        subdrops.sort(key=lambda d: d.name)
        results = []
        for sdrop in subdrops:
            results.append(ingest_drop(conn, registry, sdrop, force=force))
        return {"multi_drop": results}

    # Otherwise assume it's a drop directory
    return ingest_drop(conn, registry, p, force=force)


def run_pipeline(
    drop_paths: list[str | Path],
    out_dir: str | Path,
    db_path: str | Path | None = None,
    schemas_dir: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run full pipeline: initialize DB, ingest all drops in order, and generate reports."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    registry = SchemaRegistry(Path(schemas_dir) if schemas_dir else None)

    try:
        ingest_results = []
        for d in drop_paths:
            res = ingest_arbitrary_path(conn, registry, d, force=force)
            ingest_results.append(res)

        report_paths = generate_reports(conn, registry, out_dir)
        return {
            "ingest_results": ingest_results,
            "report_paths": {k: str(v) for k, v in report_paths.items()},
        }
    finally:
        conn.close()
