"""In-file duplicate detection for employee_number + course_code pairs."""

from __future__ import annotations

from smarthris.importing.enrolment_column_mapping import enrolment_record_key
from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode


class EnrolmentDuplicateTracker:
    """First-occurrence tracker for enrolment pairs within one CSV."""

    def __init__(self) -> None:
        self._pairs: dict[str, int] = {}

    def check(
        self,
        *,
        row_number: int,
        employee_number: str,
        course_code: str,
    ) -> list[ImportRowIssue]:
        key = enrolment_record_key(employee_number, course_code)
        if not employee_number.strip() or not course_code.strip():
            return []

        first_row = self._pairs.get(key)
        if first_row is None:
            self._pairs[key] = row_number
            return []

        return [
            ImportRowIssue(
                row_number=row_number,
                code=ImportErrorCode.DUPLICATE_IN_FILE.value,
                message=(
                    f"Duplicate enrolment '{key}' "
                    f"(first seen on row {first_row})"
                ),
                field="employee_number",
                employee_number=employee_number.strip(),
                record_key=key,
            )
        ]
