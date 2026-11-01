"""Streaming employee CSV generator for performance benchmarks."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from smarthris.config import Settings, get_settings
from smarthris.performance.paths import performance_csv_dir

CSV_HEADER = (
    "Employee Number",
    "First Name",
    "Last Name",
    "Work Email",
    "Department ID",
    "Job Role",
    "Job Title",
    "Hire Date",
    "Status",
)


@dataclass(frozen=True, slots=True)
class GeneratedCsv:
    path: Path
    row_count: int
    file_size_bytes: int
    employee_number_prefix: str
    invalid_row_count: int
    department_id: int


def generate_employee_csv(
    *,
    row_count: int,
    department_id: int,
    destination: Path | None = None,
    employee_number_prefix: str | None = None,
    invalid_percent: float = 0.0,
    inject_duplicate_every: int | None = None,
    settings: Settings | None = None,
    run_id: str | None = None,
) -> GeneratedCsv:
    """
    Stream ``row_count`` employee rows to disk (no full in-memory row list).

    ``invalid_percent`` (0–100) controls how many rows get an invalid email.
    ``inject_duplicate_every`` when set (e.g. 100) repeats employee_number/email
    of the previous clean row on every Nth data row.
    """
    if row_count < 0:
        raise ValueError("row_count must be >= 0")
    if not 0.0 <= invalid_percent <= 100.0:
        raise ValueError("invalid_percent must be between 0 and 100")
    if inject_duplicate_every is not None and inject_duplicate_every < 2:
        raise ValueError("inject_duplicate_every must be >= 2 when set")

    cfg = settings or get_settings()
    prefix = employee_number_prefix or f"PERF-{run_id or uuid4().hex[:8]}"
    dest = destination or (performance_csv_dir() / f"{prefix}-{row_count}.csv")
    dest.parent.mkdir(parents=True, exist_ok=True)

    invalid_budget = int(row_count * (invalid_percent / 100.0))
    invalid_written = 0
    invalid_stride = max(1, row_count // invalid_budget) if invalid_budget else 0
    last_clean_number: str | None = None
    last_clean_email: str | None = None

    with dest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for index in range(1, row_count + 1):
            employee_number = f"{prefix}-{index:06d}"
            email = f"{prefix.lower()}.{index:06d}@example.com"
            work_email = email

            is_invalid = (
                invalid_budget > 0
                and invalid_written < invalid_budget
                and index % invalid_stride == 0
            )
            is_duplicate = (
                not is_invalid
                and inject_duplicate_every is not None
                and index % inject_duplicate_every == 0
                and last_clean_number is not None
                and last_clean_email is not None
            )

            if is_invalid:
                work_email = "not-an-email"
                invalid_written += 1
            elif is_duplicate:
                employee_number = last_clean_number
                work_email = last_clean_email
            else:
                last_clean_number = employee_number
                last_clean_email = work_email

            writer.writerow(
                [
                    employee_number,
                    "Perf",
                    f"User{index:06d}",
                    work_email,
                    department_id,
                    "Engineer",
                    "Software Engineer",
                    "2020-01-15",
                    "active",
                ]
            )

    file_size = dest.stat().st_size
    if file_size > cfg.max_upload_bytes:
        dest.unlink(missing_ok=True)
        raise ValueError(
            f"Generated CSV is {file_size} bytes, exceeding max_upload_bytes="
            f"{cfg.max_upload_bytes}. Reduce row_count or widen the limit for benches only."
        )

    return GeneratedCsv(
        path=dest,
        row_count=row_count,
        file_size_bytes=file_size,
        employee_number_prefix=prefix,
        invalid_row_count=invalid_written,
        department_id=department_id,
    )
