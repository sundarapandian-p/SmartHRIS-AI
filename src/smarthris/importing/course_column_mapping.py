"""Map spreadsheet headers to canonical course import columns."""

from dataclasses import dataclass, field

from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode

CANONICAL_COURSE_CODE = "course_code"
CANONICAL_COURSE_NAME = "course_name"
CANONICAL_CATEGORY = "category"
CANONICAL_DESCRIPTION = "description"
CANONICAL_DURATION_HOURS = "duration_hours"
CANONICAL_STATUS = "status"

REQUIRED_CANONICAL_COLUMNS = (
    CANONICAL_COURSE_CODE,
    CANONICAL_COURSE_NAME,
    CANONICAL_CATEGORY,
    CANONICAL_DURATION_HOURS,
)

SUPPORTED_CANONICAL_COLUMNS = frozenset(
    {
        CANONICAL_COURSE_CODE,
        CANONICAL_COURSE_NAME,
        CANONICAL_CATEGORY,
        CANONICAL_DESCRIPTION,
        CANONICAL_DURATION_HOURS,
        CANONICAL_STATUS,
    }
)

_HEADER_ALIASES: dict[str, str] = {
    "course code": CANONICAL_COURSE_CODE,
    "course_code": CANONICAL_COURSE_CODE,
    "course id": CANONICAL_COURSE_CODE,
    "course_id": CANONICAL_COURSE_CODE,
    "course name": CANONICAL_COURSE_NAME,
    "course_name": CANONICAL_COURSE_NAME,
    "title": CANONICAL_COURSE_NAME,
    "category": CANONICAL_CATEGORY,
    "description": CANONICAL_DESCRIPTION,
    "duration hours": CANONICAL_DURATION_HOURS,
    "duration_hours": CANONICAL_DURATION_HOURS,
    "duration": CANONICAL_DURATION_HOURS,
    "status": CANONICAL_STATUS,
}

HEADER_ROW_NUMBER = 1


def normalize_header(header: str) -> str:
    return " ".join(header.strip().split()).lower()


def resolve_canonical_header(header: str) -> str | None:
    normalized = normalize_header(header)
    if not normalized:
        return None
    return _HEADER_ALIASES.get(normalized)


@dataclass(frozen=True, slots=True)
class CourseHeaderMappingResult:
    column_to_original: dict[str, str]
    extra_headers: tuple[str, ...] = ()
    issues: tuple[ImportRowIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.issues


def map_course_headers(raw_headers: list[str]) -> CourseHeaderMappingResult:
    column_to_original: dict[str, str] = {}
    extras: list[str] = []
    issues: list[ImportRowIssue] = []

    for raw in raw_headers:
        original = raw.strip()
        if not original:
            continue
        canonical = resolve_canonical_header(raw)
        if canonical is None:
            extras.append(raw)
            continue
        if canonical in column_to_original:
            issues.append(
                ImportRowIssue(
                    row_number=HEADER_ROW_NUMBER,
                    code=ImportErrorCode.INVALID_VALUE.value,
                    field=canonical,
                    message=(
                        f"Duplicate column mapping for '{canonical}': "
                        f"'{column_to_original[canonical]}' and '{raw}'"
                    ),
                )
            )
            continue
        column_to_original[canonical] = raw

    for required in REQUIRED_CANONICAL_COLUMNS:
        if required not in column_to_original:
            issues.append(
                ImportRowIssue(
                    row_number=HEADER_ROW_NUMBER,
                    code=ImportErrorCode.MISSING_REQUIRED.value,
                    field=required,
                    message=f"Missing required column header for '{required}'",
                )
            )

    return CourseHeaderMappingResult(
        column_to_original=column_to_original,
        extra_headers=tuple(extras),
        issues=tuple(issues),
    )


def map_course_row_values(
    row_number: int,
    raw_cells: dict[str, str],
    header_mapping: CourseHeaderMappingResult,
) -> ParsedRow:
    values: dict[str, str] = {}
    for canonical, original_header in header_mapping.column_to_original.items():
        if original_header in raw_cells:
            values[canonical] = raw_cells[original_header]
        else:
            values[canonical] = ""
    return ParsedRow(row_number=row_number, values=values)
