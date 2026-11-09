"""Validate-only course import row pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from smarthris.config import get_settings
from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.course_duplicate_tracker import CourseDuplicateTracker
from smarthris.importing.validation.course_row_schema import (
    CourseImportRow,
    parse_course_import_row,
)
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.repositories.course_repository import CourseRepository


@dataclass(frozen=True, slots=True)
class ValidatedCourseImportRow:
    row_number: int
    row: CourseImportRow


@dataclass(frozen=True, slots=True)
class CourseRowValidationResult:
    row_number: int
    validated: ValidatedCourseImportRow | None
    issues: tuple[ImportRowIssue, ...]

    @property
    def is_valid(self) -> bool:
        return self.validated is not None and not self.issues


@dataclass
class _PendingCourseRow:
    row_number: int
    parsed: CourseImportRow


class CourseImportRowValidator:
    """
    Validate ParsedRow streams without inserting courses.

    Schema parse → in-file duplicates → DB existence checks in batches.
    """

    def __init__(
        self,
        *,
        course_repository: CourseRepository,
        batch_size: int | None = None,
    ) -> None:
        self._courses = course_repository
        self._batch_size = batch_size if batch_size is not None else get_settings().import_batch_size
        self._duplicates = CourseDuplicateTracker()

    def validate(self, rows: Iterator[ParsedRow]) -> Iterator[CourseRowValidationResult]:
        pending: list[_PendingCourseRow] = []

        for parsed_row in rows:
            early = self._validate_until_db(parsed_row)
            if isinstance(early, CourseRowValidationResult):
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
    ) -> CourseRowValidationResult | _PendingCourseRow:
        row_number = parsed_row.row_number
        model, schema_issues = parse_course_import_row(row_number, parsed_row.values)
        if model is None:
            return CourseRowValidationResult(
                row_number=row_number,
                validated=None,
                issues=tuple(schema_issues),
            )

        dup_issues = self._duplicates.check(
            row_number=row_number,
            course_code=model.course_code,
        )
        if dup_issues:
            return CourseRowValidationResult(
                row_number=row_number,
                validated=None,
                issues=tuple(dup_issues),
            )

        return _PendingCourseRow(row_number=row_number, parsed=model)

    def _flush_pending(
        self,
        pending: list[_PendingCourseRow],
    ) -> Iterator[CourseRowValidationResult]:
        codes = [item.parsed.course_code for item in pending]
        existing = self._courses.find_existing_course_codes(codes)

        for item in pending:
            code = item.parsed.course_code
            if code in existing:
                yield CourseRowValidationResult(
                    row_number=item.row_number,
                    validated=None,
                    issues=(
                        ImportRowIssue(
                            row_number=item.row_number,
                            code=ImportErrorCode.ALREADY_EXISTS.value,
                            message=f"Course '{code}' already exists",
                            field="course_code",
                            record_key=code,
                        ),
                    ),
                )
                continue
            yield CourseRowValidationResult(
                row_number=item.row_number,
                validated=ValidatedCourseImportRow(
                    row_number=item.row_number,
                    row=item.parsed,
                ),
                issues=(),
            )
