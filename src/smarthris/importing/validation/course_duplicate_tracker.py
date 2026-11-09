"""In-file duplicate detection for course_code."""

from __future__ import annotations

from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode


def normalize_course_code(value: str) -> str:
    return value.strip()


class CourseDuplicateTracker:
    """Stateful first-occurrence tracker for course_code within one CSV."""

    def __init__(self) -> None:
        self._codes: dict[str, int] = {}

    def check(self, *, row_number: int, course_code: str) -> list[ImportRowIssue]:
        normalized = normalize_course_code(course_code)
        if not normalized:
            return []

        first_row = self._codes.get(normalized)
        if first_row is None:
            self._codes[normalized] = row_number
            return []

        return [
            ImportRowIssue(
                row_number=row_number,
                code=ImportErrorCode.DUPLICATE_IN_FILE.value,
                message=(
                    f"Duplicate course_code '{normalized}' "
                    f"(first seen on row {first_row})"
                ),
                field="course_code",
                record_key=normalized,
            )
        ]
