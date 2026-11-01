"""Map spreadsheet headers to canonical employee import columns."""

from dataclasses import dataclass, field

from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode

# Canonical column names used throughout import processing.
CANONICAL_EMPLOYEE_NUMBER = "employee_number"
CANONICAL_FIRST_NAME = "first_name"
CANONICAL_LAST_NAME = "last_name"
CANONICAL_WORK_EMAIL = "work_email"
CANONICAL_DEPARTMENT_ID = "department_id"
CANONICAL_DEPARTMENT_NAME = "department_name"
CANONICAL_JOB_ROLE = "job_role"
CANONICAL_JOB_TITLE = "job_title"
CANONICAL_MANAGER_EMPLOYEE_NUMBER = "manager_employee_number"
CANONICAL_HIRE_DATE = "hire_date"
CANONICAL_STATUS = "status"

REQUIRED_CANONICAL_COLUMNS = (
    CANONICAL_EMPLOYEE_NUMBER,
    CANONICAL_FIRST_NAME,
    CANONICAL_LAST_NAME,
    CANONICAL_WORK_EMAIL,
)

DEPARTMENT_CANONICAL_COLUMNS = (CANONICAL_DEPARTMENT_ID, CANONICAL_DEPARTMENT_NAME)

SUPPORTED_CANONICAL_COLUMNS = frozenset(
    {
        CANONICAL_EMPLOYEE_NUMBER,
        CANONICAL_FIRST_NAME,
        CANONICAL_LAST_NAME,
        CANONICAL_WORK_EMAIL,
        CANONICAL_DEPARTMENT_ID,
        CANONICAL_DEPARTMENT_NAME,
        CANONICAL_JOB_ROLE,
        CANONICAL_JOB_TITLE,
        CANONICAL_MANAGER_EMPLOYEE_NUMBER,
        CANONICAL_HIRE_DATE,
        CANONICAL_STATUS,
    }
)

# Normalized header text -> canonical column (aliases).
_HEADER_ALIASES: dict[str, str] = {
    "employee number": CANONICAL_EMPLOYEE_NUMBER,
    "employee id": CANONICAL_EMPLOYEE_NUMBER,
    "employee_number": CANONICAL_EMPLOYEE_NUMBER,
    "employee_id": CANONICAL_EMPLOYEE_NUMBER,
    "first name": CANONICAL_FIRST_NAME,
    "first_name": CANONICAL_FIRST_NAME,
    "last name": CANONICAL_LAST_NAME,
    "last_name": CANONICAL_LAST_NAME,
    "work email": CANONICAL_WORK_EMAIL,
    "email": CANONICAL_WORK_EMAIL,
    "work_email": CANONICAL_WORK_EMAIL,
    "department id": CANONICAL_DEPARTMENT_ID,
    "department_id": CANONICAL_DEPARTMENT_ID,
    "department name": CANONICAL_DEPARTMENT_NAME,
    "department_name": CANONICAL_DEPARTMENT_NAME,
    "job role": CANONICAL_JOB_ROLE,
    "job_role": CANONICAL_JOB_ROLE,
    "job title": CANONICAL_JOB_TITLE,
    "job_title": CANONICAL_JOB_TITLE,
    "manager employee number": CANONICAL_MANAGER_EMPLOYEE_NUMBER,
    "manager_employee_number": CANONICAL_MANAGER_EMPLOYEE_NUMBER,
    "hire date": CANONICAL_HIRE_DATE,
    "hire_date": CANONICAL_HIRE_DATE,
    "status": CANONICAL_STATUS,
}

HEADER_ROW_NUMBER = 1


def normalize_header(header: str) -> str:
    """Strip whitespace and lowercase for alias lookup."""
    return " ".join(header.strip().split()).lower()


def resolve_canonical_header(header: str) -> str | None:
    """Return canonical column name for a raw header, or None if unsupported."""
    normalized = normalize_header(header)
    if not normalized:
        return None
    return _HEADER_ALIASES.get(normalized)


@dataclass(frozen=True, slots=True)
class HeaderMappingResult:
    """Outcome of mapping a file header row."""

    # canonical column -> original header text from the file
    column_to_original: dict[str, str]
    # original headers that were not mapped to a supported column
    extra_headers: tuple[str, ...] = ()
    issues: tuple[ImportRowIssue, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.issues


def map_headers(raw_headers: list[str]) -> HeaderMappingResult:
    """
    Map raw header labels to canonical import columns.

    Detects duplicate canonical mappings, missing required headers, and ignores
    unsupported extra columns (listed in ``extra_headers``).
    """
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

    if not any(col in column_to_original for col in DEPARTMENT_CANONICAL_COLUMNS):
        issues.append(
            ImportRowIssue(
                row_number=HEADER_ROW_NUMBER,
                code=ImportErrorCode.MISSING_REQUIRED.value,
                field="department",
                message=(
                    "Missing department column: provide 'Department ID' and/or "
                    "'Department Name' header"
                ),
            )
        )

    return HeaderMappingResult(
        column_to_original=column_to_original,
        extra_headers=tuple(extras),
        issues=tuple(issues),
    )


def map_row_values(
    row_number: int,
    raw_cells: dict[str, str],
    header_mapping: HeaderMappingResult,
) -> ParsedRow:
    """
    Build a ParsedRow using canonical keys from a raw dict keyed by original headers.

    ``raw_cells`` keys should match original header strings from the file.
    """
    values: dict[str, str] = {}
    for canonical, original_header in header_mapping.column_to_original.items():
        if original_header in raw_cells:
            values[canonical] = raw_cells[original_header]
        else:
            values[canonical] = ""
    return ParsedRow(row_number=row_number, values=values)
