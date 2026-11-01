"""Phase 3.2 Step 3 — duplicate tracker, department resolver, row validator (unit)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from smarthris.importing.types import ParsedRow
from smarthris.importing.validation.department_resolver import DepartmentResolver
from smarthris.importing.validation.duplicate_tracker import DuplicateTracker
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.importing.validation.row_validator import ImportRowValidator
from smarthris.repositories.department_repository import normalize_department_name


def _dept(dept_id: int, name: str) -> SimpleNamespace:
    return SimpleNamespace(id=dept_id, department_name=name)


def _parsed(row_number: int, **values: str) -> ParsedRow:
    base = {
        "employee_number": "E1",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada@example.com",
        "department_id": "1",
    }
    base.update(values)
    return ParsedRow(row_number=row_number, values=base)


# --- DuplicateTracker --------------------------------------------------------


def test_duplicate_tracker_keeps_first_occurrence() -> None:
    tracker = DuplicateTracker()
    assert tracker.check(row_number=2, employee_number="E1", work_email="a@x.com") == []
    assert tracker.first_row_for_employee_number("E1") == 2
    assert tracker.first_row_for_work_email("a@x.com") == 2


def test_duplicate_tracker_employee_number() -> None:
    tracker = DuplicateTracker()
    tracker.check(row_number=2, employee_number="E1", work_email="a@x.com")
    issues = tracker.check(row_number=5, employee_number=" E1 ", work_email="b@x.com")
    assert len(issues) == 1
    assert issues[0].code == ImportErrorCode.DUPLICATE_IN_FILE.value
    assert issues[0].field == "employee_number"
    assert issues[0].row_number == 5
    assert tracker.first_row_for_employee_number("E1") == 2


def test_duplicate_tracker_email_case_insensitive() -> None:
    tracker = DuplicateTracker()
    tracker.check(row_number=2, employee_number="E1", work_email="Ada@Example.COM")
    issues = tracker.check(row_number=3, employee_number="E2", work_email="  ada@example.com ")
    assert len(issues) == 1
    assert issues[0].code == ImportErrorCode.DUPLICATE_IN_FILE.value
    assert issues[0].field == "work_email"
    assert tracker.first_row_for_work_email("ADA@EXAMPLE.COM") == 2


# --- DepartmentResolver ------------------------------------------------------


def test_normalize_department_name_trims_and_lowercases() -> None:
    assert normalize_department_name("  Eng   ineering ") == "eng ineering"


def test_department_resolver_id_only() -> None:
    repo = MagicMock()
    repo.list_all.return_value = [_dept(1, "Engineering")]
    resolver = DepartmentResolver(repo)
    result = resolver.resolve(
        row_number=2, department_id=1, department_name=None, employee_number="E1"
    )
    assert result.department_id == 1
    assert result.issues == ()


def test_department_resolver_name_only_case_insensitive() -> None:
    repo = MagicMock()
    repo.list_all.return_value = [_dept(7, "Engineering")]
    resolver = DepartmentResolver(repo)
    result = resolver.resolve(
        row_number=2,
        department_id=None,
        department_name="  engineering ",
        employee_number="E1",
    )
    assert result.department_id == 7
    assert result.issues == ()


def test_department_resolver_both_match() -> None:
    repo = MagicMock()
    repo.list_all.return_value = [_dept(3, "Sales")]
    resolver = DepartmentResolver(repo)
    result = resolver.resolve(
        row_number=2, department_id=3, department_name="sales", employee_number="E1"
    )
    assert result.department_id == 3


def test_department_resolver_mismatch() -> None:
    repo = MagicMock()
    repo.list_all.return_value = [_dept(1, "Engineering"), _dept(2, "Sales")]
    resolver = DepartmentResolver(repo)
    result = resolver.resolve(
        row_number=4, department_id=1, department_name="Sales", employee_number="E9"
    )
    assert result.department_id is None
    assert result.issues[0].code == ImportErrorCode.DEPARTMENT_MISMATCH.value
    assert result.issues[0].row_number == 4


def test_department_resolver_unknown_id_and_name() -> None:
    repo = MagicMock()
    repo.list_all.return_value = [_dept(1, "Engineering")]
    resolver = DepartmentResolver(repo)
    unknown_id = resolver.resolve(
        row_number=2, department_id=99, department_name=None, employee_number="E1"
    )
    assert unknown_id.issues[0].code == ImportErrorCode.UNKNOWN_DEPARTMENT.value
    assert unknown_id.issues[0].field == "department_id"

    unknown_name = resolver.resolve(
        row_number=3, department_id=None, department_name="Nope", employee_number="E2"
    )
    assert unknown_name.issues[0].code == ImportErrorCode.UNKNOWN_DEPARTMENT.value
    assert unknown_name.issues[0].field == "department_name"


def test_department_resolver_list_all_once() -> None:
    repo = MagicMock()
    repo.list_all.return_value = [_dept(1, "Engineering")]
    resolver = DepartmentResolver(repo)
    resolver.resolve(row_number=2, department_id=1, department_name=None, employee_number="E1")
    resolver.resolve(row_number=3, department_id=1, department_name=None, employee_number="E2")
    assert repo.list_all.call_count == 1
    assert resolver.load_count == 1


# --- ImportRowValidator ------------------------------------------------------


def _validator(
    *,
    departments: list[SimpleNamespace] | None = None,
    existing_numbers: set[str] | None = None,
    existing_emails: set[str] | None = None,
    batch_size: int = 500,
) -> tuple[ImportRowValidator, MagicMock, MagicMock]:
    dept_repo = MagicMock()
    dept_repo.list_all.return_value = departments or [_dept(1, "Engineering")]
    emp_repo = MagicMock()
    emp_repo.find_existing_employee_numbers.return_value = existing_numbers or set()
    emp_repo.find_existing_work_emails.return_value = existing_emails or set()
    validator = ImportRowValidator(
        department_repository=dept_repo,
        employee_repository=emp_repo,
        batch_size=batch_size,
    )
    return validator, dept_repo, emp_repo


def test_row_validator_success_preserves_row_number() -> None:
    validator, dept_repo, emp_repo = _validator()
    results = list(validator.validate(iter([_parsed(7)])))
    assert len(results) == 1
    assert results[0].is_valid
    assert results[0].row_number == 7
    assert results[0].validated is not None
    assert results[0].validated.row_number == 7
    assert results[0].validated.resolved_department_id == 1
    assert dept_repo.list_all.call_count == 1
    emp_repo.find_existing_employee_numbers.assert_called_once()
    emp_repo.find_existing_work_emails.assert_called_once()


def test_row_validator_duplicate_across_batches() -> None:
    validator, _, emp_repo = _validator(batch_size=1)
    rows = iter(
        [
            _parsed(2, employee_number="E1", work_email="a@x.com"),
            _parsed(3, employee_number="E1", work_email="b@x.com"),
        ]
    )
    results = list(validator.validate(rows))
    assert results[0].is_valid
    assert results[1].issues[0].code == ImportErrorCode.DUPLICATE_IN_FILE.value
    # Second row failed before DB flush; only first batch queried.
    assert emp_repo.find_existing_employee_numbers.call_count == 1


def test_row_validator_schema_invalid_skips_db_lookup() -> None:
    validator, _, emp_repo = _validator()
    bad = ParsedRow(
        row_number=2,
        values={
            "employee_number": "",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "work_email": "ada@example.com",
            "department_id": "1",
        },
    )
    results = list(validator.validate(iter([bad])))
    assert not results[0].is_valid
    emp_repo.find_existing_employee_numbers.assert_not_called()
    emp_repo.find_existing_work_emails.assert_not_called()


def test_row_validator_already_exists_number_and_email() -> None:
    validator, _, _ = _validator(
        existing_numbers={"E1"},
        existing_emails={"ada@example.com"},
    )
    results = list(validator.validate(iter([_parsed(2)])))
    codes = {i.code for i in results[0].issues}
    fields = {i.field for i in results[0].issues}
    assert ImportErrorCode.ALREADY_EXISTS.value in codes
    assert "employee_number" in fields
    assert "work_email" in fields


def test_row_validator_name_only_department() -> None:
    validator, _, _ = _validator(departments=[_dept(5, "People Ops")])
    row = _parsed(2, department_id="", department_name="people ops")
    # remove empty department_id key noise — use explicit values
    row = ParsedRow(
        row_number=2,
        values={
            "employee_number": "E5",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "work_email": "ada5@example.com",
            "department_name": "people ops",
        },
    )
    results = list(validator.validate(iter([row])))
    assert results[0].is_valid
    assert results[0].validated is not None
    assert results[0].validated.resolved_department_id == 5


def test_row_validator_streams_without_materializing_input() -> None:
    validator, _, emp_repo = _validator(batch_size=2)

    def generate() -> Iterator[ParsedRow]:
        for i in range(5):
            yield _parsed(
                i + 2,
                employee_number=f"E{i}",
                work_email=f"u{i}@example.com",
            )

    count = 0
    for result in validator.validate(generate()):
        assert result.row_number == count + 2
        count += 1
    assert count == 5
    # 2 + 2 + 1 flushes
    assert emp_repo.find_existing_employee_numbers.call_count == 3
