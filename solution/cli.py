"""Command-line interface for Assignment A: Interaction Ledger & Reporting Pipeline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.config import DATABASE_PATH, SCHEMAS_DIR
from solution.database import get_db_connection, init_db
from solution.pipeline import ingest_arbitrary_path, run_pipeline
from solution.reporting import generate_reports
from solution.validation import SchemaRegistry


def build_parser() -> argparse.ArgumentParser:
    common_parent = argparse.ArgumentParser(add_help=False)
    common_parent.add_argument(
        "--db",
        default=str(DATABASE_PATH),
        help="Path to SQLite database file (default: agentic.db)",
    )
    common_parent.add_argument(
        "--schemas",
        default=str(SCHEMAS_DIR),
        help="Directory containing schemas and dispositions.csv",
    )

    parser = argparse.ArgumentParser(
        prog="python -m solution",
        description="Agentic Universe - Interaction Ledger & Reporting Pipeline (Assignment A)",
        parents=[common_parent],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. init-db
    subparsers.add_parser("init-db", parents=[common_parent], help="Initialize the database schema")

    # 2. ingest <path>
    ingest_parser = subparsers.add_parser(
        "ingest",
        parents=[common_parent],
        help="Ingest a drop directory, CSV lead file, or JSONL event file",
    )
    ingest_parser.add_argument("path", help="Path to drop folder or data file")
    ingest_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-ingest even if file hash matches ingested_files record",
    )

    # 3. report --out <dir>
    report_parser = subparsers.add_parser(
        "report",
        parents=[common_parent],
        help="Generate all five contract reports",
    )
    report_parser.add_argument(
        "--out",
        required=True,
        help="Output directory for generated reports",
    )

    # 4. run-pipeline --drops <drop1> [<drop2>...] --out <dir>
    pipeline_parser = subparsers.add_parser(
        "run-pipeline",
        parents=[common_parent],
        help="Run end-to-end pipeline: init-db, ingest drops, and generate reports",
    )
    pipeline_parser.add_argument(
        "--drops",
        nargs="+",
        required=True,
        help="List of drop directories to ingest in order",
    )
    pipeline_parser.add_argument(
        "--out",
        required=True,
        help="Output directory for generated reports",
    )
    pipeline_parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-ingestion of already processed files",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    schemas_path = Path(args.schemas)

    if args.command == "init-db":
        init_db(db_path)
        print(f"Database initialized successfully at {db_path}")
        return 0

    if args.command == "ingest":
        init_db(db_path)
        conn = get_db_connection(db_path)
        registry = SchemaRegistry(schemas_path)
        try:
            res = ingest_arbitrary_path(conn, registry, args.path, force=args.force)
            print(f"Ingested {args.path}:")
            print(res)
            return 0
        finally:
            conn.close()

    if args.command == "report":
        conn = get_db_connection(db_path)
        registry = SchemaRegistry(schemas_path)
        try:
            reports = generate_reports(conn, registry, args.out)
            print(f"Generated 5 reports in {args.out}:")
            for name, path in reports.items():
                print(f"  - {name}: {path}")
            return 0
        finally:
            conn.close()

    if args.command == "run-pipeline":
        res = run_pipeline(
            drop_paths=args.drops,
            out_dir=args.out,
            db_path=db_path,
            schemas_dir=schemas_path,
            force=args.force,
        )
        print("Pipeline execution complete.")
        print(f"Reports generated in {args.out}:")
        for name, path in res["report_paths"].items():
            print(f"  - {name}: {path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
