# Assignment A: Interaction Ledger & Reporting Pipeline

This repository contains my implementation of Assignment A: an ingestion and reporting pipeline that processes messy lead CSV uploads and asynchronous telephony call event streams into an append-only interaction ledger, strictly protects PII at rest, and generates the five daily reporting summaries required by clients.

---

## What This Solution Does

At Agentic Universe, operations teams upload batches of customer leads, while the telephony infrastructure emits asynchronous call event streams. Because real-world data is inherently messy—columns get renamed, phone numbers are formatted inconsistently, rows contain invalid dates, and events frequently arrive before leads are even uploaded—this pipeline acts as the reliable source of truth.

The pipeline handles:
1. **Lead Ingestion & Deduplication**: Parses CSV uploads with alias-matched headers, validates data in strict schema order, skips blank rows without counting them, isolates rejections into `rejections.csv`, and merges duplicate rows so that the permanent `batch_id` is preserved while non-null attributes are updated.
2. **Append-Only Interaction Ledger**: Ingests JSON Lines event streams (`call.initiated`, `call.connected`, `disposition.set`, etc.) without ever mutating past records. Overrides from supervisors are appended as fresh events, not in-place updates.
3. **Orphan Event Quarantining & Auto-Linking**: Events for numbers not yet uploaded (or events delivered in `drop_1` whose leads arrive in `drop_2`) are safely quarantined. Once the corresponding lead arrives in a later batch, the pipeline retroactively reconciles and links the events.
4. **PII Security at Rest**: Customer names and mobile numbers are never saved in plaintext. They are encrypted using Fernet (AES-128-CBC + HMAC authentication), while exact phone lookups and unique constraints use a deterministic HMAC-SHA256 blind index.
5. **IST-Aware Reporting**: Normalizes timestamps across UTC (`Z`), explicit offsets, and naive formats (treated as IST per spec), producing the exact 5 reporting artifacts: `batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`, `rejections.csv`, and `data_quality.json`.

---

## Architecture & Code Structure

The codebase is organized modularly under `solution/`:

- **`config.py`**: Settings, filesystem paths, and cryptographic secret retrieval from environment variables, plus the IST timezone definition (`UTC+05:30`).
- **`pii.py`**: PII cryptographic handling: Fernet authenticated encryption, HMAC-SHA256 blind indexing for phone lookups, and phone masking (`+91******1234`).
- **`normalization.py`**: Clean, tested parser functions for Indian mobile numbers (`+91XXXXXXXXXX`), day-first dates (supporting case-insensitive months like `01-dec-2026`), currency strings (`₹`, `Rs.`, `INR`, commas), enums, headers, and multi-format timestamps.
- **`validation.py`**: Loads JSON schemas and `dispositions.csv`. Validates row data strictly in schema order so the first failure is recorded as the row's rejection reason. Counts warnings on invalid optional fields without rejecting valid rows.
- **`database.py`**: SQLite database setup with WAL (Write-Ahead Logging) mode, foreign key enforcement, and explicit transaction context management (`transaction(conn)`).
- **`models.py`**: Simple dataclasses for leads, events, rejections, and data quality metrics.
- **`lead_ingestion.py`**: Reads CSVs, performs duplicate merging, records rejections, applies schema defaults to fields that remain null after merging, and triggers orphan reconciliation.
- **`event_ingestion.py`**: Streams JSONL event files, filters malformed lines, drops duplicates by `event_id`, flags unknown disposition codes, and links calls to leads.
- **`ledger.py`**: Queries the append-only ledger to compute call metrics and resolve effective dispositions (latest override wins, otherwise latest set disposition).
- **`reporting.py`**: Aggregates metrics and generates the 5 contract report files with exact header names, sorting orders, and decimal formatting.
- **`pipeline.py`**: High-level orchestrator that finds and ingests drops in alphabetical order.
- **`cli.py`**: Command-line entrypoint supporting `init-db`, `ingest`, `report`, and `run-pipeline`.

---

## Getting Started

### 1. Requirements & Setup
- Python 3.11+
- Virtual environment

```bash
# Set up virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# (Optional) Copy environment variables template
cp .env.example .env
```

### 2. Run Tests
The repository includes 40 unit and integration tests covering normalization, PII encryption, duplicate merging, orphan event linking, bucket precedence, and idempotent re-runs:
```bash
python -m pytest -v
```

### 3. Generate Data
Generate the synthetic dataset using the provided tool:
```bash
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data
```

### 4. Run the Ingestion & Reporting
You can run the pipeline step by step:
```bash
# Initialize SQLite database
python -m solution init-db

# Ingest drop 1 then drop 2
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2

# Generate the 5 output reports into reports/
python -m solution report --out reports/
```

Or run everything with a single end-to-end command:
```bash
python -m solution run-pipeline --drops data/drop_1 data/drop_2 --out reports/
```

---

## Output Contract & Report Descriptions

When `report --out reports/` is executed, it writes:

1. **`batch_summary.csv`**: Aggregates total leads and bucket counts (`yet_to_reach_out`, `in_progress_telephony_failed`, `in_progress_dialed`, `completed`) grouped and sorted by `workflow_id, batch_id`.
2. **`daily_overview.csv`**: Call volume, connected calls, connectivity rate formatted to exactly 4 decimals (e.g., `0.5625`), and distinct leads with a successful outcome, sorted by `workflow_id, date`.
3. **`lead_report.csv`**: Detailed status per lead, sorted by `workflow_id, external_ref`. Includes the masked phone number, assigned bucket, total calls, connected calls, latest call time in IST, current effective disposition, and a boolean indicating whether the lead ever converted (`ever_success`).
4. **`rejections.csv`**: Detailed list of rejected rows, sorted by `workflow_id, batch_id, row_number`, stating the first failure reason (e.g. `invalid_phone`, `missing_required:customer_name`). Contains zero PII.
5. **`data_quality.json`**: Operational audit summary showing total lines read, rejected rows, duplicate rows merged, field warnings, malformed event lines, duplicate event IDs dropped, unknown dispositions, and the final count of quarantined orphan events.

---

## Engineering Design & Key Decisions

- **Why SQLite with WAL mode?** It requires zero external database servers to run or review, satisfies the durability requirement, supports atomic transactions, and performs sequential writes extremely fast.
- **Why Blind Indexing with HMAC-SHA256?** We need to prevent plaintext phone numbers from ever sitting in database tables. However, we also need to match incoming telephony events to leads efficiently and enforce unique constraints per workflow. Storing a deterministic HMAC-SHA256 hash allows indexed lookups without exposing raw numbers.
- **How are out-of-order events handled?** If call events arrive in `drop_1` but the client doesn't upload the lead CSV until `drop_2`, the events are stored in quarantine (`is_orphan=1`). When the lead CSV is processed, an automatic reconciliation query matches those events to the newly created lead.
- **Why file-hash idempotency?** Re-ingesting a drop that has already been processed checks the SHA-256 hash in `ingested_files`. If the file is unchanged, the pipeline skips redundant work and avoids inflating duplicate counts or modifying existing records.

---

## Production Readiness Note

If scaling this system to handle 100k+ leads and millions of events per day in production:
1. **Database Migration**: The schema is ANSI SQL compatible and transitions directly to PostgreSQL. Ingestion throughput can be increased using `COPY` or `INSERT ... ON CONFLICT DO UPDATE` batching.
2. **Streaming Ingestion**: Instead of batch JSONL files, the event ingestion module can be hooked into a Kafka or RabbitMQ consumer to process call events in real-time.
3. **Key Management**: Encryption and HMAC keys can be pulled from AWS KMS, GCP Cloud KMS, or HashiCorp Vault with support for multi-version key rotation.
4. **Monitoring**: Ingest metrics from `ingested_files` can be exported directly to Prometheus / Datadog dashboards to monitor lead rejection spikes, telephony carrier failure rates, and orphaned event queues.
