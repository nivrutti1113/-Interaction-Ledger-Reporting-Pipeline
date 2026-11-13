# AI Usage Disclosure

## Tools used
Antigravity (built-in AI coding assistant) with Gemini model.

## What I used them for
- Scaffolding the initial project module structure under `solution/` (`config.py`, `models.py`, `database.py`, `pii.py`, `normalization.py`, `validation.py`, `lead_ingestion.py`, `event_ingestion.py`, `ledger.py`, `reporting.py`, `pipeline.py`, `cli.py`).
- Generating boilerplate regex patterns for multi-format Indian date matching and currency prefix parsing.
- Drafting initial pytest test cases for normalization and schema validation edge cases.

## What I wrote or decided myself
- **Core Architecture & Data Modeling**: Designed the SQLite schema with WAL mode, foreign keys, and indexes. Structured the append-only ledger schema to support orphan event isolation and retroactive reconciliation.
- **PII Security Architecture**: Chose Fernet authenticated symmetric encryption (AES-128-CBC + HMAC-SHA256) for recoverable customer names and phone numbers, and deterministic HMAC-SHA256 blind indexing for phone lookup and unique constraints.
- **Orphan Reconciliation Logic**: Designed the two-stage reconciliation query that links previously orphaned events either by matching `(workflow_id, phone_hmac)` or through `call_id` propagation for supervisor overrides.
- **Reporting & Business Logic**: Implemented the derived call facts, chronological effective disposition resolution (latest override > latest set, ignoring unknown catalogue codes), and the four-tier bucket precedence rules.
- **Idempotency Strategy**: Implemented SHA-256 file-level hashing and transaction management to ensure repeated ingestion of identical drops produces no duplicate records and leaves metrics unaltered.

## Where the AI was wrong or unhelpful
- **Argparse Subcommand Argument Parsing**: The initial CLI parser placed common flags (`--db`, `--schemas`) only on the root parser, which caused `argparse` to fail when flags were passed after the subcommand (e.g. `python -m solution init-db --db test.db`). I fixed this by introducing a shared `common_parent` parser with `add_help=False` that is inherited by both the root parser and all subparsers, enabling flags before and after subcommands.
- **Rejection Reason Precedence**: An early draft checked phone formatting before checking if `customer_name` was missing. The specification strictly requires walking fields in schema order and taking the *first* failure reason. I corrected this to iterate through fields in exact schema order so that `missing_required:customer_name` takes precedence if `customer_name` is empty.
- **Warning vs. Rejection on Optional Fields**: The AI initially treated invalid optional dates or currency strings as row rejections. The specification explicitly distinguishes between required field failures (which reject the row) and optional field failures (which store null and count one `field_warning` only for non-rejected rows). I adjusted the validator to ensure optional field invalidities only increment `field_warnings` and store `None`.

## How I verified AI-generated code
- **Automated Pytest Suite**: Ran 40 unit and integration tests covering phone canonicalization, date parsing, currency formatting, enum normalization, PII roundtrips, HMAC determinism, duplicate lead merging, event deduplication, orphan reconciliation, and report generation.
- **Diff Comparison on Re-runs**: Ran `Compare-Object` across all five generated report files before and after re-ingesting drops to verify 100% byte-for-byte reproducibility.
- **Fresh Seed Verification**: Ran the pipeline against a different seed (`--seed 99`) to confirm the pipeline generalizes cleanly to unseen synthetic data without hardcoded assumptions.
- **Database Inspection**: Executed raw SQLite queries to verify that no plaintext customer names or phone numbers were stored in the database.
- **Line-by-line Code Review**: Read through every module to verify typing, error handling, transaction boundaries, and compliance with the specification.
