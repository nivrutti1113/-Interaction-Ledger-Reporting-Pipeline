# AI Usage Disclosure

AI tools are allowed. We want to know **how** you used them, because using them well is a skill. Honest answers are never held against you. Discovering undisclosed use is.

## Tools used
- Claude / ChatGPT / Cursor (AI coding assistants for exploration, scaffolding, and drafting test scenarios)

## What I used them for
- Setting up the initial boilerplate directory structure under `solution/` (`config.py`, `models.py`, `database.py`, `normalization.py`, etc.).
- Drafting regex snippets for messy string patterns (such as parsing `DD-Mon-YYYY` dates case-insensitively and stripping currency symbols like `₹`, `Rs.`, `INR` with commas).
- Brainstorming edge cases for Indian phone formatting (leading zero handling, landline prefixes, 11/12-digit strings) and writing initial test fixtures.
- Generating draft boilerplate code for `argparse` subcommands.

## What I wrote or decided myself
- **Core Architecture & Data Modeling**:
  - Designed the SQLite schema with WAL mode, foreign key constraints, and performance indexes.
  - Formulated the append-only interaction ledger approach where `disposition.override` records are treated as new chronological events rather than in-place database updates.
- **PII Protection Strategy**:
  - Decided on Fernet symmetric authenticated encryption (`AES-128-CBC` + `HMAC-SHA256`) for recoverable customer names and phone numbers.
  - Designed the deterministic `HMAC-SHA256` blind-indexing mechanism for phone numbers, enabling fast unique constraints and event-to-lead matching without ever storing or querying plaintext numbers.
- **Orphan Reconciliation Logic**:
  - Designed the retroactive reconciliation queries that link quarantined orphan events (`is_orphan = 1`) when their corresponding lead arrives in a later batch or drop.
  - Implemented the `call_id` fallback matching logic for supervisor overrides that lack phone numbers.
- **Business Logic & Reporting Contract**:
  - Enforced the strict 4-level bucket precedence hierarchy (`yet_to_reach_out` -> `completed` -> `in_progress_telephony_failed` -> `in_progress_dialed`).
  - Implemented the effective disposition resolution rules (latest valid override by `occurred_at`, falling back to latest valid set disposition, while filtering out unknown disposition codes).
  - Designed the idempotency mechanism using file-level SHA-256 hashes so that duplicate ingest runs don't alter existing records or inflate duplicate-merge counters.

## Where the AI was wrong or unhelpful
1. **Validation Rejection Precedence vs. Schema Order**:
   - *Issue*: The AI initially wrote a validation helper that checked phone validity before checking if `customer_name` was present.
   - *Why it was wrong*: The specification explicitly states: *"Rejection: walk the fields in schema order. The first failure becomes the row's single reason."* In `motor_renewal`, `customer_name` comes before `phone`. If a row had both a missing name and an invalid phone, the AI's version emitted `invalid_phone` instead of `missing_required:customer_name`.
   - *Fix*: I noticed this while writing the schema validation tests. I rewrote the validator to strictly iterate over fields in the exact order defined in the schema JSON.
2. **Rejection vs. Warning on Optional Fields**:
   - *Issue*: The AI initially rejected rows that contained invalid optional dates (e.g. `31/02/2026`) or unparseable premium amounts.
   - *Why it was wrong*: The specification distinguishes between required and optional fields: an invalid optional field should simply be stored as `null` and increment `field_warnings` by 1—it must *not* reject the row.
   - *Fix*: I adjusted the parser so optional invalid values store `None` and increment the warning counter only on valid rows, preserving the leads.
3. **Argparse CLI Subcommand Flag Ordering**:
   - *Issue*: The AI generated a standard `argparse` setup where `--db` and `--schemas` were only added to the top-level parser. When running `python -m solution init-db --db test.db`, `argparse` threw an unrecognized argument error because flags appeared after the subcommand.
   - *Fix*: I restructured the parser with a shared `common_parent` parser (`add_help=False`) inherited by both the root parser and all subparsers, allowing options to be placed flexibly before or after subcommands.

## How I verified AI-generated code
- **Automated Pytest Suite**: Wrote 40 focused unit and integration tests covering phone normalisation, date parsing, currency formatting, duplicate lead merging, orphan event retroactive linking, bucket precedence, and report generation.
- **Idempotency & Diff Verification**: Processed `drop_1` and `drop_2`, generated all 5 reports, re-ingested the same drops, and ran file diffs (`Compare-Object` / diff) to confirm that re-running produced 100% byte-for-byte identical output files.
- **Unseen Seed Testing**: Generated a fresh dataset using `--seed 99` and verified that the entire pipeline executed cleanly without assumptions tied to seed 42.
- **Database PII Inspection**: Queried the SQLite database directly (`SELECT * FROM leads`, `SELECT * FROM events`) to verify that no plaintext names or phone numbers were written to any column.
- **Line-by-line Code Review**: Read through every single generated file line-by-line, enforcing strict type hinting, proper exception handling, and clean transaction rollbacks on failure.
