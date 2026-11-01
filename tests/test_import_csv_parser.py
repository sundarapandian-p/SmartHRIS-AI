"""Phase 3.2 Step 2 — streaming CSV parser."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from smarthris.importing.parsers.csv_parser import CsvParseError, parse_csv
from smarthris.importing.validation.error_codes import ImportErrorCode

VALID_HEADER = (
    "Employee Number,First Name,Last Name,Work Email,Department ID,Status\n"
)


def _valid_csv(*data_lines: str) -> str:
    body = "\n".join(data_lines)
    if body:
        return VALID_HEADER + body + "\n"
    return VALID_HEADER


def test_parse_csv_valid_multiple_rows_and_row_numbers() -> None:
    text = _valid_csv(
        "E1,Ada,Lovelace,ada@example.com,1,active",
        "E2,Grace,Hopper,grace@example.com,2,inactive",
    )
    rows = list(parse_csv(io.StringIO(text)))
    assert len(rows) == 2
    assert rows[0].row_number == 2
    assert rows[1].row_number == 3
    assert rows[0].values["employee_number"] == "E1"
    assert rows[0].values["work_email"] == "ada@example.com"
    assert rows[1].values["first_name"] == "Grace"
    assert rows[1].values["status"] == "inactive"


def test_parse_csv_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    content = (
        "\ufeffEmployee Number,First Name,Last Name,Email,Department Name\n"
        "E9,Alan,Turing,alan@example.com,Research\n"
    )
    path.write_bytes(content.encode("utf-8"))  # literal BOM as UTF-8 bytes
    rows = list(parse_csv(path))
    assert len(rows) == 1
    assert rows[0].row_number == 2
    assert rows[0].values["employee_number"] == "E9"
    assert rows[0].values["department_name"] == "Research"


def test_parse_csv_bom_on_text_stream() -> None:
    text = (
        "\ufeffEmployee Number,First Name,Last Name,Work Email,Department ID\n"
        "E1,Ada,Lovelace,ada@example.com,1\n"
    )
    rows = list(parse_csv(io.StringIO(text)))
    assert len(rows) == 1
    assert rows[0].values["employee_number"] == "E1"


def test_parse_csv_missing_required_headers() -> None:
    text = "First Name,Last Name,Email,Department ID\nAda,Lovelace,ada@example.com,1\n"
    with pytest.raises(CsvParseError) as exc_info:
        list(parse_csv(io.StringIO(text)))
    err = exc_info.value
    assert err.row_number == 1
    fields = {issue.field for issue in err.issues}
    assert "employee_number" in fields
    assert all(i.code == ImportErrorCode.MISSING_REQUIRED.value for i in err.issues)


def test_parse_csv_empty_file() -> None:
    with pytest.raises(CsvParseError) as exc_info:
        list(parse_csv(io.StringIO("")))
    assert exc_info.value.issues[0].code == ImportErrorCode.MISSING_REQUIRED.value


def test_parse_csv_header_only_yields_no_rows() -> None:
    rows = list(parse_csv(io.StringIO(VALID_HEADER)))
    assert rows == []


def test_parse_csv_malformed_quoting() -> None:
    # Unclosed quote under strict dialect → csv.Error → CsvParseError
    text = VALID_HEADER + 'E1,"Ada,Lovelace,ada@example.com,1,active\n'
    with pytest.raises(CsvParseError) as exc_info:
        list(parse_csv(io.StringIO(text)))
    assert exc_info.value.issues[0].code == ImportErrorCode.INVALID_VALUE.value
    assert exc_info.value.row_number == 2


def test_parse_csv_ignores_extra_columns() -> None:
    text = (
        "Employee Number,First Name,Last Name,Work Email,Department ID,Notes,Salary\n"
        "E1,Ada,Lovelace,ada@example.com,1,vip,100000\n"
    )
    rows = list(parse_csv(io.StringIO(text)))
    assert len(rows) == 1
    assert "Notes" not in rows[0].values
    assert "Salary" not in rows[0].values
    assert set(rows[0].values) == {
        "employee_number",
        "first_name",
        "last_name",
        "work_email",
        "department_id",
    }
    assert rows[0].values["employee_number"] == "E1"


def test_parse_csv_strips_cell_whitespace() -> None:
    text = _valid_csv("  E1  ,  Ada  , Lovelace ,  ada@example.com  ,  1  , active ")
    row = next(parse_csv(io.StringIO(text)))
    assert row.values["employee_number"] == "E1"
    assert row.values["first_name"] == "Ada"
    assert row.values["last_name"] == "Lovelace"
    assert row.values["work_email"] == "ada@example.com"
    assert row.values["department_id"] == "1"
    assert row.values["status"] == "active"


def test_parse_csv_skips_blank_data_rows() -> None:
    text = _valid_csv(
        "E1,Ada,Lovelace,ada@example.com,1,active",
        ",,,,",
        "E2,Grace,Hopper,grace@example.com,2,active",
    )
    rows = list(parse_csv(io.StringIO(text)))
    assert [r.row_number for r in rows] == [2, 4]
    assert rows[1].values["employee_number"] == "E2"


def test_parse_csv_does_not_close_caller_owned_stream() -> None:
    stream = io.StringIO(_valid_csv("E1,Ada,Lovelace,ada@example.com,1,active"))
    list(parse_csv(stream))
    assert stream.closed is False


def test_parse_csv_path_closes_owned_file(tmp_path: Path) -> None:
    path = tmp_path / "employees.csv"
    path.write_text(
        _valid_csv("E1,Ada,Lovelace,ada@example.com,1,active"),
        encoding="utf-8",
    )
    rows = list(parse_csv(path))
    assert len(rows) == 1
    # Re-open succeeds; owned handle was released after iteration.
    assert path.read_text(encoding="utf-8").startswith("Employee Number")


def test_parse_csv_is_streaming_generator(tmp_path: Path) -> None:
    path = tmp_path / "large.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(VALID_HEADER)
        for i in range(1, 501):
            handle.write(
                f"E{i},First{i},Last{i},user{i}@example.com,1,active\n"
            )

    iterator = parse_csv(path)
    assert hasattr(iterator, "__next__")
    first = next(iterator)
    assert first.row_number == 2
    assert first.values["employee_number"] == "E1"
    # Consume remaining without materializing via list in the parser itself.
    count = 1
    for row in iterator:
        count += 1
        assert row.row_number == count + 1
    assert count == 500


def test_parse_csv_aliases_employee_id_and_email() -> None:
    text = (
        "Employee ID,First Name,Last Name,Email,Department Name\n"
        "E3,Kate,Gonzalez,kate@example.com,HR\n"
    )
    row = next(parse_csv(io.StringIO(text)))
    assert row.values["employee_number"] == "E3"
    assert row.values["work_email"] == "kate@example.com"
    assert row.values["department_name"] == "HR"
