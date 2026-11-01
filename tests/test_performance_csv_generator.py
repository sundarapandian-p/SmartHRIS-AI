"""Unit tests for the streaming performance CSV generator (always run)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from smarthris.config import Settings
from smarthris.performance.csv_generator import generate_employee_csv
from smarthris.performance.db_guard import (
    UnsafePerformanceDatabaseError,
    assert_safe_performance_database_url,
)
from smarthris.performance.gate import PERFORMANCE_ENV_VAR, performance_runs_enabled


def test_generate_csv_streams_exact_row_count(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_upload_bytes=1_000_000)
    dest = tmp_path / "out.csv"
    result = generate_employee_csv(
        row_count=25,
        department_id=7,
        destination=dest,
        employee_number_prefix="PERF-UNIT",
        settings=settings,
    )
    assert result.row_count == 25
    assert result.path == dest
    assert result.file_size_bytes == dest.stat().st_size
    assert result.file_size_bytes > 0
    assert result.department_id == 7

    with dest.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    assert rows[0][0] == "Employee Number"
    assert len(rows) == 26  # header + 25


def test_generate_csv_unique_numbers_and_emails(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_upload_bytes=1_000_000)
    result = generate_employee_csv(
        row_count=40,
        department_id=1,
        destination=tmp_path / "unique.csv",
        employee_number_prefix="PERF-UNIQ",
        settings=settings,
    )
    with result.path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    numbers = [row["Employee Number"] for row in rows]
    emails = [row["Work Email"] for row in rows]
    assert len(numbers) == len(set(numbers))
    assert len(emails) == len(set(emails))
    assert all(n.startswith("PERF-UNIQ-") for n in numbers)


def test_generate_csv_invalid_percent(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_upload_bytes=1_000_000)
    result = generate_employee_csv(
        row_count=20,
        department_id=1,
        destination=tmp_path / "invalid.csv",
        invalid_percent=50.0,
        employee_number_prefix="PERF-BAD",
        settings=settings,
    )
    assert result.invalid_row_count == 10
    with result.path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    bad = [row for row in rows if row["Work Email"] == "not-an-email"]
    assert len(bad) == 10


def test_generate_csv_injects_duplicates(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_upload_bytes=1_000_000)
    result = generate_employee_csv(
        row_count=10,
        department_id=1,
        destination=tmp_path / "dups.csv",
        inject_duplicate_every=5,
        employee_number_prefix="PERF-DUP",
        settings=settings,
    )
    with result.path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    numbers = [row["Employee Number"] for row in rows]
    assert len(numbers) == 10
    assert len(set(numbers)) < 10


def test_generate_csv_rejects_oversized_file(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, max_upload_bytes=200)
    with pytest.raises(ValueError, match="max_upload_bytes"):
        generate_employee_csv(
            row_count=50,
            department_id=1,
            destination=tmp_path / "big.csv",
            settings=settings,
        )
    assert not (tmp_path / "big.csv").exists()


def test_performance_gate_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PERFORMANCE_ENV_VAR, raising=False)
    assert performance_runs_enabled() is False
    monkeypatch.setenv(PERFORMANCE_ENV_VAR, "1")
    assert performance_runs_enabled() is True
    monkeypatch.setenv(PERFORMANCE_ENV_VAR, "0")
    assert performance_runs_enabled() is False


def test_performance_db_guard_rejects_production_and_sqlite() -> None:
    with pytest.raises(UnsafePerformanceDatabaseError):
        assert_safe_performance_database_url("mysql+pymysql://u:p@127.0.0.1:3306/smarthris")
    with pytest.raises(UnsafePerformanceDatabaseError):
        assert_safe_performance_database_url("sqlite:///tmp.db")
    assert assert_safe_performance_database_url(
        "mysql+pymysql://u:p@127.0.0.1:3306/smarthris_test"
    ).endswith("smarthris_test")
    assert assert_safe_performance_database_url(
        "mysql+pymysql://u:p@127.0.0.1:3306/smarthris_bench"
    ).endswith("smarthris_bench")
