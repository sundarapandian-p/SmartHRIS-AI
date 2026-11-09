"""Map spreadsheet headers to canonical enrolment import columns."""

from dataclasses import dataclass, field

from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode

CANONICAL_EMPLOYEE_NUMBER = "employee_number"
CANONICAL_COURSE_CODE = "course_code"
CANONICAL_ENROLMENT_DATE = "enrolment_date"
CANONICAL_STATUS = "status"
CANONICAL_COMPLETION_DATE = "completion_date"
CANONICAL_SCORE = "score"

REQUIRED_CANONICAL_COLUMNS = (
    CANONICAL_EMPLOYEE_NUMBER,
    CANONICAL_COURSE_CODE,
    CANONICAL_ENROLMENT_DATE,
    CANONICAL_STATUS,
)

_HEADER_ALIASES: dict[str, str] = {
    "employee number": CANONICAL_EMPLOYEE_NUMBER,
    "employee_number": CANONICAL_EMPLOYEE_NUMBER,
    "employee id": CANONICAL_EMPLOYEE_NUMBER,
    "employee_id": CANONICAL_EMPLOYEE_NUMBER,
    "course code": CANONICAL_COURSE_CODE,
    "course_code": CANONICAL_COURSE_CODE,
    "course id": CANONICAL_COURSE_CODE,
    "course_id": CANONICAL_COURSE_CODE,
    "enrolment date": CANONICAL_ENROLMENT_DATE,
    "enrolment_date": CANONICAL_ENROLMENT_DATE,
    "enrollment date": CANONICAL_ENROLMENT_DATE,
    "enrollment_date": CANONICAL_ENROLMENT_DATE,
    "status": CANONICAL_STATUS,
    "completion date": CANONICAL_COMPLETION_DATE,
    "completion_date": CANONICAL_COMPLETION_DATE,
    "score": CANONICAL_SCORE,
}

HEADER_ROW_NUMBER = 1


def normalize_header(header: str) -> str:
    return " ".join(header.strip().split()).lower()


def resolve_canonical_header(header: str) -> str | None:
    normalized = normalize_header(header)
    if not normalized:
        return None
    return _HEADER_ALIASES.get(normalized)


def enrolment_record_key(employee_number: str, course_code: str) -> str:
    return f"{employee_number.strip()}|{course_code.strip()}"


@dataclass(frozen=True, slots=True)
class EnrolmentHeaderMappingResult:
    column_to_original: dict[str, str]
    extra_headers: tuple[str, ...] = ()
    issues: tuple[ImportRowIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.issues


def map_enrolment_headers(raw_headers: list[str]) -> EnrolmentHeaderMappingResult:
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

    return EnrolmentHeaderMappingResult(
        column_to_original=column_to_original,
        extra_headers=tuple(extras),
        issues=tuple(issues),
    )


def map_enrolment_row_values(
    row_number: int,
    raw_cells: dict[str, str],
    header_mapping: EnrolmentHeaderMappingResult,
) -> ParsedRow:
    values: dict[str, str] = {}
    for canonical, original_header in header_mapping.column_to_original.items():
        if original_header in raw_cells:
            values[canonical] = raw_cells[original_header]
        else:
            values[canonical] = ""
    return ParsedRow(row_number=row_number, values=values)
