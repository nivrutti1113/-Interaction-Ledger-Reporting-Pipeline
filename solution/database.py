"""Database connection, initialization, and transaction management."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from solution.config import DATABASE_PATH

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS ingested_files (
    file_path TEXT PRIMARY KEY,
    file_type TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    drop_name TEXT,
    ingested_at TEXT NOT NULL,
    total_lines INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    malformed_count INTEGER NOT NULL DEFAULT 0,
    unknown_disp_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    phone_hmac TEXT NOT NULL,
    phone_encrypted TEXT NOT NULL,
    phone_masked TEXT NOT NULL,
    customer_name_encrypted TEXT NOT NULL,
    external_ref TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    policy_expiry TEXT,
    premium_amount REAL,
    product TEXT,
    order_date TEXT,
    preferred_language TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT uq_workflow_phone UNIQUE (workflow_id, phone_hmac)
);

CREATE INDEX IF NOT EXISTS idx_leads_wf_ref ON leads(workflow_id, external_ref);
CREATE INDEX IF NOT EXISTS idx_leads_wf_batch ON leads(workflow_id, batch_id);

CREATE TABLE IF NOT EXISTS rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    CONSTRAINT uq_rejection UNIQUE (workflow_id, batch_id, row_number)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    phone_hmac TEXT,
    phone_encrypted TEXT,
    lead_id INTEGER,
    payload_json TEXT NOT NULL,
    disposition_code TEXT,
    is_orphan INTEGER NOT NULL DEFAULT 0,
    is_unknown_disposition INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(lead_id) REFERENCES leads(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_events_call ON events(call_id);
CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id);
CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_wf_phone ON events(workflow_id, phone_hmac);
CREATE INDEX IF NOT EXISTS idx_events_orphan ON events(is_orphan);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


def get_db_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Get a connection to SQLite database configured with row factory and pragmas."""
    target_path = Path(db_path) if db_path else DATABASE_PATH
    if str(target_path) != ":memory:":
        target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path), timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    if str(target_path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


def init_db(db_path: Path | str | None = None) -> None:
    """Initialize database schema."""
    conn = get_db_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional scope around a series of operations."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
