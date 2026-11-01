"""Validate-only import row pipeline over a stream of ParsedRow values."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from smarthris.config import get_settings
from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.department_resolver import DepartmentResolver
from smarthris.importing.validation.duplicate_tracker import DuplicateTracker
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.importing.validation.row_schema import (
    EmployeeImportRow,
    parse_employee_import_row,
)
from smarthris.repositories.department_repository import DepartmentRepository
from smarthris.repositories.employee_repository import EmployeeRepository


@dataclass(frozen=True, slots=True)
class ValidatedImportRow:
    """Schema-valid row with a resolved department_id ready for a later insert step."""

    row_number: int
    row: EmployeeImportRow
    resolved_department_id: int


@dataclass(frozen=True, slots=True)
class RowValidationResult:
    """One streamed outcome: a validated row, issues, or both (should not both succeed)."""

    row_number: int
    validated: ValidatedImportRow | None
    issues: tuple[ImportRowIssue, ...]

    @property
    def is_valid(self) -> bool:
        return self.validated is not None and not self.issues


@dataclass
class _PendingRow:
    row_number: int
    parsed: EmployeeImportRow
    resolved_department_id: int


class ImportRowValidator:
    """
    Validate ParsedRow streams without inserting employees.

    - Structural parse via ``parse_employee_import_row``
    - In-file duplicates via ``DuplicateTracker`` (stateful across batches)
    - Department resolution via ``DepartmentResolver`` (one ``list_all``)
    - DB collisions via chunked repository lookups (``import_batch_size``)
    """

    def __init__(
        self,
        *,
        department_repository: DepartmentRepository,
        employee_repository: EmployeeRepository,
        batch_size: int | None = None,
    ) -> None:
        self._employees = employee_repository
        self._batch_size = batch_size if batch_size is not None else get_settings().import_batch_size
        self._duplicates = DuplicateTracker()
        self._departments = DepartmentResolver(department_repository)

    @property
    def department_load_count(self) -> int:
        return self._departments.load_count

    def validate(self, rows: Iterator[ParsedRow]) -> Iterator[RowValidationResult]:
        """Yield one result per input row; buffer only up to ``batch_size`` for DB checks."""
        pending: list[_PendingRow] = []

        for parsed_row in rows:
            early = self._validate_until_db(parsed_row)
            if isinstance(early, RowValidationResult):
                yield early
                continue
            pending.append(early)
            if len(pending) >= self._batch_size:
                yield from self._flush_pending(pending)
                pending.clear()

        if pending:
            yield from self._flush_pending(pending)

    def _validate_until_db(
        self,
        parsed_row: ParsedRow,
    ) -> RowValidationResult | _PendingRow:
        row_number = parsed_row.row_number
        model, schema_issues = parse_employee_import_row(row_number, parsed_row.values)
        if model is None:
            return RowValidationResult(
                row_number=row_number,
                validated=None,
                issues=tuple(schema_issues),
            )

        employee_number = model.employee_number
        duplicate_issues = self._duplicates.check(
            row_number=row_number,
            employee_number=employee_number,
            work_email=str(model.work_email),
        )
        if duplicate_issues:
            return RowValidationResult(
                row_number=row_number,
                validated=None,
                issues=tuple(duplicate_issues),
            )

        resolved = self._departments.resolve(
            row_number=row_number,
            department_id=model.department_id,
            department_name=model.department_name,
            employee_number=employee_number,
        )
        if resolved.issues:
            return RowValidationResult(
                row_number=row_number,
                validated=None,
                issues=resolved.issues,
            )

        assert resolved.department_id is not None
        return _PendingRow(
            row_number=row_number,
            parsed=model,
            resolved_department_id=resolved.department_id,
        )

    def _flush_pending(self, pending: list[_PendingRow]) -> Iterator[RowValidationResult]:
        numbers = [item.parsed.employee_number for item in pending]
        emails = [str(item.parsed.work_email) for item in pending]

        existing_numbers = self._employees.find_existing_employee_numbers(numbers)
        existing_emails = self._employees.find_existing_work_emails(emails)

        for item in pending:
            issues: list[ImportRowIssue] = []
            number = item.parsed.employee_number
            email = str(item.parsed.work_email).lower()

            if number in existing_numbers:
                issues.append(
                    ImportRowIssue(
                        row_number=item.row_number,
                        code=ImportErrorCode.ALREADY_EXISTS.value,
                        message=f"employee_number '{number}' already exists",
                        field="employee_number",
                        employee_number=number,
                    )
                )
            if email in existing_emails:
                issues.append(
                    ImportRowIssue(
                        row_number=item.row_number,
                        code=ImportErrorCode.ALREADY_EXISTS.value,
                        message=f"work_email '{email}' already exists",
                        field="work_email",
                        employee_number=number,
                    )
                )

            if issues:
                yield RowValidationResult(
                    row_number=item.row_number,
                    validated=None,
                    issues=tuple(issues),
                )
                continue

            yield RowValidationResult(
                row_number=item.row_number,
                validated=ValidatedImportRow(
                    row_number=item.row_number,
                    row=item.parsed,
                    resolved_department_id=item.resolved_department_id,
                ),
                issues=(),
            )
