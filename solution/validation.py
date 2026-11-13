"""Schema-based validation and disposition loading."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solution.config import SCHEMAS_DIR
from solution.normalization import (
    normalize_enum,
    normalize_header,
    normalize_phone,
    parse_date,
    parse_decimal,
)


@dataclass(frozen=True)
class Disposition:
    workflow_id: str
    code: str
    label: str
    is_terminal: bool
    is_success: bool


class SchemaRegistry:
    def __init__(self, schemas_dir: Path | None = None):
        self.schemas_dir = schemas_dir or SCHEMAS_DIR
        self._schemas: dict[str, dict[str, Any]] = {}
        self._dispositions: dict[tuple[str, str], Disposition] = {}
        self._load_all()

    def _load_all(self) -> None:
        # Load workflow schemas (*.json)
        for json_file in self.schemas_dir.glob("*.json"):
            with open(json_file, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
                wf_id = schema_data.get("workflow_id")
                if wf_id:
                    self._schemas[wf_id] = schema_data

        # Load dispositions.csv
        disp_file = self.schemas_dir / "dispositions.csv"
        if disp_file.exists():
            with open(disp_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    wf = row["workflow_id"].strip()
                    code = row["code"].strip()
                    label = row["label"].strip()
                    is_term = row["is_terminal"].strip().lower() in ("true", "1", "yes")
                    is_succ = row["is_success"].strip().lower() in ("true", "1", "yes")
                    disp = Disposition(
                        workflow_id=wf,
                        code=code,
                        label=label,
                        is_terminal=is_term,
                        is_success=is_succ,
                    )
                    self._dispositions[(wf, code)] = disp

    def get_schema(self, workflow_id: str) -> dict[str, Any] | None:
        return self._schemas.get(workflow_id)

    def get_disposition(self, workflow_id: str, code: str) -> Disposition | None:
        return self._dispositions.get((workflow_id, code))

    def is_known_disposition(self, workflow_id: str, code: str) -> bool:
        return (workflow_id, code) in self._dispositions

    def get_reference_field_name(self, workflow_id: str) -> str:
        """Find the schema field name marked reference: true."""
        schema = self.get_schema(workflow_id)
        if schema:
            for field in schema.get("fields", []):
                if field.get("reference"):
                    return field["name"]
        return "external_ref"


def build_header_mapping(csv_headers: list[str], schema_fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    Map raw CSV header columns to schema fields based on normalized names and aliases.
    Ignores columns that match nothing.
    Returns: dict mapping raw_header -> schema_field_def
    """
    # Build lookup of normalized token -> field_def
    token_to_field: dict[str, dict[str, Any]] = {}
    for field in schema_fields:
        norm_name = normalize_header(field["name"])
        token_to_field[norm_name] = field
        for alias in field.get("aliases", []):
            token_to_field[normalize_header(alias)] = field

    mapping: dict[str, dict[str, Any]] = {}
    for header in csv_headers:
        norm_h = normalize_header(header)
        if norm_h in token_to_field:
            mapping[header] = token_to_field[norm_h]
    return mapping


@dataclass
class ValidationResult:
    is_valid: bool
    reason: str | None = None
    cleaned_fields: dict[str, Any] | None = None
    field_warnings: int = 0


def validate_lead_row(
    raw_row: dict[str, str],
    header_mapping: dict[str, dict[str, Any]],
    schema_fields: list[dict[str, Any]],
) -> ValidationResult:
    """
    Validate a single CSV row against schema rules.
    - Walk fields in schema order.
    - First failure becomes rejection reason.
    - Optional invalid fields store None and increment field_warnings (counted only if row is not rejected).
    """
    # Map raw row values into field_name -> raw_val
    raw_by_field_name: dict[str, str] = {}
    for raw_header, raw_val in raw_row.items():
        field_def = header_mapping.get(raw_header)
        if field_def:
            raw_by_field_name[field_def["name"]] = raw_val

    cleaned_fields: dict[str, Any] = {}
    field_warnings = 0

    # Walk fields strictly in schema order
    for field in schema_fields:
        fname = field["name"]
        ftype = field.get("type", "string")
        is_required = field.get("required", False)
        raw_val = raw_by_field_name.get(fname)
        trimmed = raw_val.strip() if raw_val is not None else ""

        # Check missing
        if trimmed == "":
            if is_required:
                return ValidationResult(
                    is_valid=False,
                    reason=f"missing_required:{fname}",
                    field_warnings=0,
                )
            cleaned_fields[fname] = None
            continue

        # Validate by type
        if ftype == "phone":
            canonical = normalize_phone(trimmed)
            if canonical is None:
                return ValidationResult(
                    is_valid=False,
                    reason="invalid_phone",
                    field_warnings=0,
                )
            cleaned_fields[fname] = canonical

        elif ftype == "date":
            parsed = parse_date(trimmed)
            if parsed is None:
                if is_required:
                    return ValidationResult(
                        is_valid=False,
                        reason=f"missing_required:{fname}",
                        field_warnings=0,
                    )
                cleaned_fields[fname] = None
                field_warnings += 1
            else:
                cleaned_fields[fname] = parsed

        elif ftype == "decimal":
            parsed_dec = parse_decimal(trimmed)
            if parsed_dec is None:
                if is_required:
                    return ValidationResult(
                        is_valid=False,
                        reason=f"missing_required:{fname}",
                        field_warnings=0,
                    )
                cleaned_fields[fname] = None
                field_warnings += 1
            else:
                cleaned_fields[fname] = parsed_dec

        elif ftype == "enum":
            allowed = field.get("values", [])
            parsed_enum = normalize_enum(trimmed, allowed)
            if parsed_enum is None:
                if is_required:
                    return ValidationResult(
                        is_valid=False,
                        reason=f"missing_required:{fname}",
                        field_warnings=0,
                    )
                cleaned_fields[fname] = None
                field_warnings += 1
            else:
                cleaned_fields[fname] = parsed_enum

        else:  # string
            cleaned_fields[fname] = trimmed

    return ValidationResult(
        is_valid=True,
        reason=None,
        cleaned_fields=cleaned_fields,
        field_warnings=field_warnings,
    )
