"""Validate enrolment CSV rows with batched employee/course resolution."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from smarthris.config import get_settings
from smarthris.importing.enrolment_column_mapping import enrolment_record_key
from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.enrolment_duplicate_tracker import (
    EnrolmentDuplicateTracker,
)
from smarthris.importing.validation.enrolment_row_schema import (
    EnrolmentImportRow,
    parse_enrolment_import_row,
)
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.repositories.course_enrolment_repository import CourseEnrolmentRepository
from smarthris.repositories.course_repository import CourseRepository
from smarthris.repositories.employee_repository import EmployeeRepository


@dataclass(frozen=True, slots=True)
class ValidatedEnrolmentImportRow:
    row_number: int
    row: EnrolmentImportRow
    employee_id: int
    course_id: int


@dataclass(frozen=True, slots=True)
class EnrolmentRowValidationResult:
    row_number: int
    validated: ValidatedEnrolmentImportRow | None
    issues: tuple[ImportRowIssue, ...]

    @property
    def is_valid(self) -> bool:
        return self.validated is not None and not self.issues


@dataclass
class _PendingEnrolmentRow:
    row_number: int
    parsed: EnrolmentImportRow


class EnrolmentImportRowValidator:
    """
    Schema → in-file duplicates → batched resolve → existing pair checks.

    Never creates employees or courses.
    """

    def __init__(
        self,
        *,
        employee_repository: EmployeeRepository,
        course_repository: CourseRepository,
        enrolment_repository: CourseEnrolmentRepository,
        batch_size: int | None = None,
    ) -> None:
        self._employees = employee_repository
        self._courses = course_repository
        self._enrolments = enrolment_repository
        self._batch_size = batch_size if batch_size is not None else get_settings().import_batch_size
        self._duplicates = EnrolmentDuplicateTracker()

    def validate(self, rows: Iterator[ParsedRow]) -> Iterator[EnrolmentRowValidationResult]:
        pending: list[_PendingEnrolmentRow] = []

        for parsed_row in rows:
            early = self._validate_until_db(parsed_row)
            if isinstance(early, EnrolmentRowValidationResult):
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
    ) -> EnrolmentRowValidationResult | _PendingEnrolmentRow:
        row_number = parsed_row.row_number
        model, schema_issues = parse_enrolment_import_row(row_number, parsed_row.values)
        if model is None:
            return EnrolmentRowValidationResult(
                row_number=row_number,
                validated=None,
                issues=tuple(schema_issues),
            )

        dup_issues = self._duplicates.check(
            row_number=row_number,
            employee_number=model.employee_number,
            course_code=model.course_code,
        )
        if dup_issues:
            return EnrolmentRowValidationResult(
                row_number=row_number,
                validated=None,
                issues=tuple(dup_issues),
            )

        return _PendingEnrolmentRow(row_number=row_number, parsed=model)

    def _flush_pending(
        self,
        pending: list[_PendingEnrolmentRow],
    ) -> Iterator[EnrolmentRowValidationResult]:
        numbers = [item.parsed.employee_number for item in pending]
        codes = [item.parsed.course_code for item in pending]
        employee_ids = self._employees.map_employee_numbers_to_ids(numbers)
        course_ids = self._courses.map_course_codes_to_ids(codes)

        resolved: list[tuple[_PendingEnrolmentRow, int, int]] = []
        for item in pending:
            emp_no = item.parsed.employee_number
            course_code = item.parsed.course_code
            record_key = enrolment_record_key(emp_no, course_code)
            employee_id = employee_ids.get(emp_no)
            course_id = course_ids.get(course_code)

            issues: list[ImportRowIssue] = []
            if employee_id is None:
                issues.append(
                    ImportRowIssue(
                        row_number=item.row_number,
                        code=ImportErrorCode.UNKNOWN_EMPLOYEE.value,
                        message=f"Employee '{emp_no}' was not found",
                        field="employee_number",
                        employee_number=emp_no,
                        record_key=record_key,
                    )
                )
            if course_id is None:
                issues.append(
                    ImportRowIssue(
                        row_number=item.row_number,
                        code=ImportErrorCode.UNKNOWN_COURSE.value,
                        message=f"Course '{course_code}' was not found",
                        field="course_code",
                        employee_number=emp_no,
                        record_key=record_key,
                    )
                )
            if issues:
                yield EnrolmentRowValidationResult(
                    row_number=item.row_number,
                    validated=None,
                    issues=tuple(issues),
                )
                continue
            assert employee_id is not None and course_id is not None
            resolved.append((item, employee_id, course_id))

        if not resolved:
            return

        pairs = [(employee_id, course_id) for _, employee_id, course_id in resolved]
        existing_pairs = self._enrolments.find_existing_pairs(pairs)

        for item, employee_id, course_id in resolved:
            record_key = enrolment_record_key(
                item.parsed.employee_number,
                item.parsed.course_code,
            )
            if (employee_id, course_id) in existing_pairs:
                yield EnrolmentRowValidationResult(
                    row_number=item.row_number,
                    validated=None,
                    issues=(
                        ImportRowIssue(
                            row_number=item.row_number,
                            code=ImportErrorCode.ALREADY_EXISTS.value,
                            message=(
                                f"Enrolment '{record_key}' already exists"
                            ),
                            field="employee_number",
                            employee_number=item.parsed.employee_number,
                            record_key=record_key,
                        ),
                    ),
                )
                continue
            yield EnrolmentRowValidationResult(
                row_number=item.row_number,
                validated=ValidatedEnrolmentImportRow(
                    row_number=item.row_number,
                    row=item.parsed,
                    employee_id=employee_id,
                    course_id=course_id,
                ),
                issues=(),
            )
