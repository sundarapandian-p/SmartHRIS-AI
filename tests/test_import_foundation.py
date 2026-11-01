"""Phase 3.2 Step 1 — import error codes, column mapping, and row schema."""

from smarthris.importing.column_mapping import (
    HeaderMappingResult,
    map_headers,
    map_row_values,
    normalize_header,
)
from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.importing.validation.row_schema import (
    EmployeeImportRow,
    parse_employee_import_row,
)
from smarthris.models.employee import EmployeeStatus


def _valid_values(**overrides: str) -> dict[str, str]:
    values = {
        "employee_number": "E100",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada@example.com",
        "department_id": "1",
    }
    values.update(overrides)
    return values


# --- error codes -------------------------------------------------------------


def test_import_error_codes_are_stable() -> None:
    assert ImportErrorCode.MISSING_REQUIRED == "missing_required"
    assert ImportErrorCode.INVALID_EMAIL == "invalid_email"
    assert ImportErrorCode.INVALID_STATUS == "invalid_status"
    assert ImportErrorCode.INVALID_DATE == "invalid_date"
    assert ImportErrorCode.DUPLICATE_IN_FILE == "duplicate_in_file"
    assert ImportErrorCode.ALREADY_EXISTS == "already_exists"
    assert ImportErrorCode.UNKNOWN_DEPARTMENT == "unknown_department"
    assert ImportErrorCode.DEPARTMENT_MISMATCH == "department_mismatch"
    assert ImportErrorCode.INVALID_VALUE == "invalid_value"
    assert len(ImportErrorCode) == 9


# --- column mapping ----------------------------------------------------------


def test_normalize_header_collapses_whitespace_and_case() -> None:
    assert normalize_header("  Employee   Number  ") == "employee number"


def test_map_headers_accepts_aliases_and_ignores_extras() -> None:
    result = map_headers(
        [
            "Employee ID",
            "First Name",
            "Last Name",
            "Email",
            "Department Name",
            "Job Role",
            "Job Title",
            "Manager Employee Number",
            "Hire Date",
            "Status",
            "Notes",
            "Salary",
        ]
    )
    assert result.is_valid
    assert result.column_to_original["employee_number"] == "Employee ID"
    assert result.column_to_original["work_email"] == "Email"
    assert result.column_to_original["department_name"] == "Department Name"
    assert result.extra_headers == ("Notes", "Salary")


def test_map_headers_detects_duplicate_canonical_columns() -> None:
    result = map_headers(
        [
            "Employee Number",
            "Employee ID",
            "First Name",
            "Last Name",
            "Work Email",
            "Department ID",
        ]
    )
    assert not result.is_valid
    codes = [issue.code for issue in result.issues]
    assert ImportErrorCode.INVALID_VALUE.value in codes
    dup = next(i for i in result.issues if i.field == "employee_number")
    assert "Employee Number" in dup.message
    assert "Employee ID" in dup.message


def test_map_headers_detects_missing_required_headers() -> None:
    result = map_headers(["First Name", "Last Name", "Email", "Department ID"])
    assert not result.is_valid
    fields = {i.field for i in result.issues}
    assert "employee_number" in fields
    assert all(i.code == ImportErrorCode.MISSING_REQUIRED.value for i in result.issues)


def test_map_headers_requires_department_column() -> None:
    result = map_headers(
        ["Employee Number", "First Name", "Last Name", "Work Email"]
    )
    assert not result.is_valid
    dept_issues = [i for i in result.issues if i.field == "department"]
    assert len(dept_issues) == 1
    assert dept_issues[0].code == ImportErrorCode.MISSING_REQUIRED.value


def test_map_row_values_uses_original_headers() -> None:
    mapping = map_headers(
        ["Employee Number", "First Name", "Last Name", "Email", "Department ID"]
    )
    parsed = map_row_values(
        2,
        {
            "Employee Number": "E1",
            "First Name": "Ada",
            "Last Name": "Lovelace",
            "Email": "ada@example.com",
            "Department ID": "3",
        },
        mapping,
    )
    assert isinstance(parsed, ParsedRow)
    assert parsed.row_number == 2
    assert parsed.values == {
        "employee_number": "E1",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada@example.com",
        "department_id": "3",
    }


# --- row schema --------------------------------------------------------------


def test_parse_row_success_defaults_status_and_strips() -> None:
    row, issues = parse_employee_import_row(
        5,
        _valid_values(
            first_name="  Ada  ",
            last_name="  Lovelace ",
            work_email="  Ada@Example.COM ",
            status="",
        ),
    )
    assert issues == []
    assert row is not None
    assert row.first_name == "Ada"
    assert row.last_name == "Lovelace"
    assert row.work_email == "ada@example.com"
    assert row.status == EmployeeStatus.ACTIVE
    assert row.department_id == 1


def test_parse_row_accepts_department_name_only() -> None:
    values = _valid_values()
    del values["department_id"]
    values["department_name"] = " Engineering "
    row, issues = parse_employee_import_row(3, values)
    assert issues == []
    assert row is not None
    assert row.department_name == "Engineering"
    assert row.department_id is None


def test_parse_row_missing_required_fields() -> None:
    _, issues = parse_employee_import_row(
        2,
        {
            "employee_number": "",
            "first_name": "",
            "last_name": "",
            "work_email": "",
            "department_id": "1",
        },
    )
    codes_by_field = {i.field: i.code for i in issues}
    assert codes_by_field["employee_number"] == ImportErrorCode.MISSING_REQUIRED.value
    assert codes_by_field["first_name"] == ImportErrorCode.MISSING_REQUIRED.value
    assert codes_by_field["last_name"] == ImportErrorCode.MISSING_REQUIRED.value
    assert codes_by_field["work_email"] == ImportErrorCode.MISSING_REQUIRED.value


def test_parse_row_invalid_email() -> None:
    _, issues = parse_employee_import_row(2, _valid_values(work_email="not-an-email"))
    assert len(issues) == 1
    assert issues[0].code == ImportErrorCode.INVALID_EMAIL.value
    assert issues[0].field == "work_email"
    assert issues[0].employee_number == "E100"
    assert issues[0].row_number == 2


def test_parse_row_invalid_status() -> None:
    _, issues = parse_employee_import_row(2, _valid_values(status="pending"))
    assert len(issues) == 1
    assert issues[0].code == ImportErrorCode.INVALID_STATUS.value
    assert issues[0].field == "status"


def test_parse_row_requires_department_reference() -> None:
    values = _valid_values(department_id="", department_name="")
    _, issues = parse_employee_import_row(2, values)
    assert any(i.code == ImportErrorCode.MISSING_REQUIRED.value for i in issues)
    assert any(i.field == "department" for i in issues)


def test_parse_row_invalid_date() -> None:
    _, issues = parse_employee_import_row(2, _valid_values(hire_date="31/12/2024"))
    assert len(issues) == 1
    assert issues[0].code == ImportErrorCode.INVALID_DATE.value
    assert issues[0].field == "hire_date"


def test_parse_row_invalid_department_id() -> None:
    _, issues = parse_employee_import_row(2, _valid_values(department_id="abc"))
    assert len(issues) == 1
    assert issues[0].code == ImportErrorCode.INVALID_VALUE.value
    assert issues[0].field == "department_id"


def test_employee_import_row_lengths_match_create_contract() -> None:
    def max_len(field_name: str) -> int:
        for item in EmployeeImportRow.model_fields[field_name].metadata:
            if hasattr(item, "max_length"):
                return int(item.max_length)
        raise AssertionError(f"no max_length on {field_name}")

    assert max_len("employee_number") == 50
    assert max_len("first_name") == 100
    assert max_len("last_name") == 100
    assert max_len("job_role") == 100
    assert max_len("job_title") == 150
    assert max_len("manager_employee_number") == 50


def test_import_row_issue_type_shape() -> None:
    issue = ImportRowIssue(
        row_number=1,
        code=ImportErrorCode.DUPLICATE_IN_FILE.value,
        message="duplicate employee_number in file",
        field="employee_number",
        employee_number="E1",
    )
    assert issue.row_number == 1
    assert isinstance(issue, ImportRowIssue)


def test_header_mapping_result_preserves_original_header_on_duplicate() -> None:
    result = map_headers(
        [
            "Employee Number",
            "Employee ID",
            "First Name",
            "Last Name",
            "Work Email",
            "Department ID",
        ]
    )
    assert isinstance(result, HeaderMappingResult)
    # First mapping wins for column_to_original; second raises issue with both originals.
    assert result.column_to_original["employee_number"] == "Employee Number"
