"""In-file duplicate detection for employee_number and work_email."""

from __future__ import annotations

from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode


def normalize_employee_number(value: str) -> str:
    return value.strip()


def normalize_work_email(value: str) -> str:
    return value.strip().lower()


class DuplicateTracker:
    """
    Stateful first-occurrence tracker for one import file.

    Survives across validation batches; never replaces the first row number.
    """

    def __init__(self) -> None:
        self._employee_numbers: dict[str, int] = {}
        self._work_emails: dict[str, int] = {}

    def check(
        self,
        *,
        row_number: int,
        employee_number: str | None,
        work_email: str | None,
    ) -> list[ImportRowIssue]:
        """
        Record first seen keys; return issues for later duplicates.

        Keys are registered even when a duplicate issue is also produced for the
        other field on the same row. First occurrence is never overwritten.
        """
        issues: list[ImportRowIssue] = []
        normalized_number = (
            normalize_employee_number(employee_number) if employee_number is not None else ""
        )
        normalized_email = normalize_work_email(work_email) if work_email is not None else ""

        if normalized_number:
            first_row = self._employee_numbers.get(normalized_number)
            if first_row is None:
                self._employee_numbers[normalized_number] = row_number
            else:
                issues.append(
                    ImportRowIssue(
                        row_number=row_number,
                        code=ImportErrorCode.DUPLICATE_IN_FILE.value,
                        message=(
                            f"Duplicate employee_number '{normalized_number}' "
                            f"(first seen on row {first_row})"
                        ),
                        field="employee_number",
                        employee_number=normalized_number,
                    )
                )

        if normalized_email:
            first_row = self._work_emails.get(normalized_email)
            if first_row is None:
                self._work_emails[normalized_email] = row_number
            else:
                issues.append(
                    ImportRowIssue(
                        row_number=row_number,
                        code=ImportErrorCode.DUPLICATE_IN_FILE.value,
                        message=(
                            f"Duplicate work_email '{normalized_email}' "
                            f"(first seen on row {first_row})"
                        ),
                        field="work_email",
                        employee_number=normalized_number or None,
                    )
                )

        return issues

    def first_row_for_employee_number(self, employee_number: str) -> int | None:
        return self._employee_numbers.get(normalize_employee_number(employee_number))

    def first_row_for_work_email(self, work_email: str) -> int | None:
        return self._work_emails.get(normalize_work_email(work_email))
