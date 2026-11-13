# Submission

## Candidate
- **Name:** Nivrutti
- **Assignment chosen:** A (Data & Interaction Ledger)
- **Time spent (honest estimate):** ~7h

## How to run
```bash
# 1. Environment setup from a clean clone
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

pip install -r requirements.txt

# 2. Run the test suite (all unit, integration, idempotency, edge-case tests)
python -m pytest -v

# 3. Generate sample data using the provided generator
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# 4. Ingest drops incrementally and generate reports
python -m solution init-db
python -m solution ingest data/drop_1
python -m solution ingest data/drop_2
python -m solution report --out reports/

# Alternatively, run the end-to-end pipeline in one command:
# python -m solution run-pipeline --drops data/drop_1 data/drop_2 --out reports/
```

## Design

### Architecture
The solution is organized into modular single-responsibility components under `solution/`:
1. **Config & Security (`config.py`, `pii.py`)**: Manages environment variables and secrets. Implements authenticated encryption via Fernet (AES-128-CBC + HMAC-SHA256) for customer names and phones, deterministic HMAC-SHA256 for blind index phone lookup and uniqueness constraints, and masking (`+91******XXXX`).
2. **Normalization & Validation (`normalization.py`, `validation.py`)**: Normalizes Indian mobile phone numbers (`+91XXXXXXXXXX`), day-first dates (with case-insensitive month names), currency amounts, case-insensitive enums, headers, and multi-format timestamps into IST. Validates rows in strict schema order, outputting the first failure as the rejection reason.
3. **Storage & Models (`database.py`, `models.py`)**: SQLite with Write-Ahead Logging (WAL) and foreign keys enabled. Encapsulates file-level ingestion tracking, leads, rejections, and the append-only event ledger within atomic transactions.
4. **Lead & Event Ingestion (`lead_ingestion.py`, `event_ingestion.py`)**: Implements alias-aware header mapping, non-blank row counting, duplicate lead merging (first `batch_id` permanently preserved, non-null fields overwrite), post-merge default application, and orphan event quarantining with automatic backward-linking when leads arrive later.
5. **Ledger & Reporting (`ledger.py`, `reporting.py`, `pipeline.py`, `cli.py`)**: Computes call facts, effective dispositions (latest override > latest set), lead buckets according to strict precedence rules, and outputs all five contract files (`batch_summary.csv`, `daily_overview.csv`, `lead_report.csv`, `rejections.csv`, `data_quality.json`).

### Database Schema & Append-Only Ledger
- **`leads` Table**: Uniquely identified by `(workflow_id, phone_hmac)`. Stores `phone_hmac` for blind indexed lookups and uniqueness, `phone_encrypted` and `customer_name_encrypted` for recoverable PII, and `phone_masked` for reporting. Stores the first `batch_id` permanently and merges subsequent updates.
- **`events` Table (Append-Only Ledger)**: Stores raw events without mutating past entries. Even when human supervisors submit `disposition.override`, the original `disposition.set` remains untouched. Derived call states and effective dispositions are evaluated chronologically via SQL/ledger queries.
- **`rejections` Table**: Tracks row-level rejections per `(workflow_id, batch_id, row_number)` with zero PII stored.
- **`ingested_files` Table**: Stores SHA-256 file hashes and metrics for idempotent re-ingestion and consistent data quality accounting across drops.

---

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **Deterministic HMAC-SHA256 for Phone Lookups** | Protects phone numbers at rest while allowing exact indexed lookup and unique constraint enforcement across batches and event linking without decrypting every database record. | Plaintext phone numbers (violates spec) or non-deterministic salted hashes (would require decrypting all leads to match incoming events). |
| **Fernet Authenticated Encryption for PII** | Combines standard AES-128-CBC encryption with HMAC-SHA256 authentication to ensure ciphertext confidentiality and tampering detection. | Unauthenticated AES-CBC (vulnerable to padding oracle attacks) or plain hashing (irrecoverable for operations). |
| **Orphan Event Quarantine in Ledger** | Events arriving before their lead (or overrides before call initiation) are preserved in the ledger with `is_orphan = 1`. A post-ingestion reconciliation links them as soon as their lead is uploaded. | Dropping unlinked events (violates "never silently discard an event" and prevents retroactive reconciliation) or caching only in memory (fragile against process crashes). |
| **File-Level SHA-256 Idempotency Tracking** | Allows fast idempotent re-ingest: if a drop or file is processed a second time with the same hash, it is safely skipped without double-counting metrics or re-evaluating unchanged rows. | Always parsing the file and relying solely on database upserts (slower and causes duplicate merge counter increments on re-runs). |
| **SQLite with WAL Mode** | Zero external infrastructure, durable ACID transactions, fast sequential writes (easily handles 100k+ rows), and cross-platform reproducibility. | PostgreSQL (added Docker/network setup overhead for reviewers without functional difference for this scale). |
| **Strict Schema-Order First Failure Rejection** | Spec explicitly mandates walking fields in schema order and recording the first failure reason (e.g. `missing_required:customer_name` before checking phone). | Collecting all validation errors in a list (rejected because spec expects a single reason string per row). |
| **Effective Disposition Evaluation** | Evaluated as the latest valid `disposition.override` by `occurred_at`. If no override exists, the latest valid `disposition.set` by `occurred_at` is used. Unknown disposition codes are ignored and do not affect the call outcome. | Overwriting the record in-place (violates append-only ledger requirement). |
| **Bucket Precedence Order** | Strict precedence: (1) no calls -> `yet_to_reach_out`; (2) any terminal call -> `completed`; (3) every call telephony failed -> `in_progress_telephony_failed`; (4) else -> `in_progress_dialed`. | Calculating buckets based on the most recent call only (incorrect per spec, which checks any terminal call and all telephony-failed calls). |

---

## Failure modes considered

1. **Events Arriving Before Leads**:
   - *Handling*: Quarantined with `lead_id = NULL` and `is_orphan = 1`. When `drop_2` introduces the corresponding leads, reconciliation automatically updates `lead_id` and clears `is_orphan`.
2. **Overrides with No Phone**:
   - *Handling*: `disposition.override` events lack a phone field. They link via `call_id` to the lead identified by other events in that call. If the call itself was an orphan, both the call events and the override link together when the lead is ingested.
3. **Malformed Event Lines & Unknown Dispositions**:
   - *Handling*: Malformed lines (invalid JSON or missing `event_id`) are caught gracefully, counted in `malformed_events`, and skipped. Unknown disposition codes are isolated, counted in `unknown_disposition_events`, and ignored for call outcome calculations.
4. **Duplicate Events and Duplicate Lead Rows**:
   - *Handling*: Duplicate `event_id` occurrences within or across files/drops are dropped and counted in `duplicate_events_dropped`. Duplicate lead rows update only non-null attributes, retain the permanent first `batch_id`, and increment `duplicate_lead_rows_merged`.
5. **Partial Batch Ingestion & Process Crashes**:
   - *Handling*: Ingestion of every file is executed within a database transaction context (`transaction(conn)`). If a file read or process fails halfway, the entire transaction rolls back cleanly, preventing orphan/corrupt partial states.
6. **Re-running Ingestion (Idempotency)**:
   - *Handling*: SHA-256 file hashes stored in `ingested_files` detect identical runs and safely return cached operational statistics, guaranteeing reports remain identical after repeated execution.

---

## Testing

40 automated tests implemented in `tests/`:
- **Normalization (`test_normalization.py`)**: 10-digit canonicalization, prefixes (`+91`, `91`, `0`), landline/bad prefix rejection, day-first dates with case-insensitive month names (`01-dec-2026`), impossible dates (`31/02`), currency prefixes (`₹`, `Rs`, `INR`, commas, rounding), and multi-format timestamps.
- **PII Protection (`test_pii.py`)**: Authenticated encryption/decryption roundtrip, deterministic HMAC-SHA256 matching, and phone masking (`+91******XXXX`).
- **Validation (`test_validation.py`)**: Schema loading, header alias mapping, schema-order rejection precedence, invalid phone rejection, and optional field warnings without rejecting rows.
- **Lead Ingestion (`test_lead_ingestion.py`)**: Single and multi-batch duplicate merging, permanent first `batch_id` preservation, and non-blank row counting with blank row skipping.
- **Event Ingestion (`test_event_ingestion.py`)**: Duplicate `event_id` dropping, malformed line handling, unknown disposition handling, and initial orphan quarantining.
- **Ledger & Reporting (`test_ledger_and_reporting.py`)**: Backward orphan event linking when leads arrive later, override precedence over set dispositions, bucket precedence rules, and verification of all five reports.
- **Idempotency & Transactions (`test_pipeline_and_idempotency.py`)**: Transaction rollback on simulated failures, idempotent re-ingestion, and incremental drop processing.

---

## Results

### Timings & Performance
- **Small Scale (`seed=42`, ~2,000 leads, ~8,700 events across 2 drops)**:
  - Database initialization: ~0.05s
  - Drop 1 Ingestion: ~3.5s
  - Drop 2 Ingestion: ~3.8s
  - Report Generation (all 5 files): ~0.4s
  - Total pipeline execution: < 8s
- **Test Suite**: 40 passed in 9.45s.
- **Generalizability Test (`seed=99`)**: Pipeline executed seamlessly with identical contract compliance.

### Data Observations
- In the generated dataset, orphaned events arrived in `drop_1` whose leads were not uploaded until `drop_2` (e.g. batch_003). The reconciliation step successfully linked 910 previously orphaned events upon `drop_2` ingestion.
- Overrides correctly adjusted outcomes (e.g. converting `payment_link_sent` to `renewed`) without mutating earlier events in the ledger.

---

## If I had another day
1. **PostgreSQL / Distributed Warehouse Ingestion**: Add an environment toggle to switch between SQLite and PostgreSQL, utilizing Postgres `COPY` or `INSERT ... ON CONFLICT DO UPDATE` for massive scale (1M+ events).
2. **Streaming Event Ingestion with Kafka / Redpanda**: Decouple file-based batch drops with a real-time event consumer that writes to the append-only ledger continuously with micro-batch windowing.
3. **Automated Key Rotation Support**: Implement a key management service (KMS) wrapper that supports multi-version HMAC and encryption key rotation with background re-encryption jobs.
4. **FastAPI Inspection Dashboard**: Add a read-only local web dashboard for operations teams to search leads, view interaction timelines, and inspect data quality trends.
