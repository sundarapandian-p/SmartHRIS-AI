"""Streaming CSV parser for employee bulk import (stdlib csv only)."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from smarthris.importing.column_mapping import map_headers, map_row_values
from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode

_BOM = "\ufeff"


class _StrictExcel(csv.Dialect):
    """Excel-compatible dialect that rejects malformed quoting."""

    delimiter = ","
    quotechar = '"'
    escapechar = None
    doublequote = True
    skipinitialspace = False
    lineterminator = "\r\n"
    quoting = csv.QUOTE_MINIMAL
    strict = True


csv.register_dialect("smarthris_csv", _StrictExcel)


class CsvParseError(Exception):
    """
    Fatal CSV structure or header failure.

    Safer than letting raw ``csv.Error`` bubble: issues use stable import codes
    so later job error persistence can reuse them.
    """

    def __init__(
        self,
        message: str,
        *,
        issues: tuple[ImportRowIssue, ...] = (),
        row_number: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.issues = issues
        self.row_number = row_number


def parse_csv(source: str | Path | TextIO) -> Iterator[ParsedRow]:
    """
    Stream ``ParsedRow`` values from a CSV path or text stream.

    - Paths are opened as UTF-8 with BOM support (``utf-8-sig``) and closed by
      the parser when iteration ends or the generator is closed.
    - Caller-owned streams are never closed.
    - Header row is row 1; the first data row is row 2.
    - Cell values are stripped; unsupported extra columns are ignored.
    - Header validation uses ``map_headers`` from Step 1.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from _iter_parsed_rows(handle)
        return
    yield from _iter_parsed_rows(source)


def _prepare_headers(header_cells: list[str]) -> list[str]:
    headers = [cell.strip() for cell in header_cells]
    if headers and headers[0].startswith(_BOM):
        headers[0] = headers[0].lstrip(_BOM).strip()
    return headers


def _iter_parsed_rows(stream: TextIO) -> Iterator[ParsedRow]:
    reader = csv.reader(stream, dialect="smarthris_csv")
    try:
        header_cells = next(reader)
    except StopIteration as exc:
        raise CsvParseError(
            "CSV file is empty",
            issues=(
                ImportRowIssue(
                    row_number=1,
                    code=ImportErrorCode.MISSING_REQUIRED.value,
                    message="CSV file is empty; expected a header row",
                ),
            ),
            row_number=1,
        ) from exc
    except csv.Error as exc:
        raise CsvParseError(
            f"Malformed CSV near header: {exc}",
            issues=(
                ImportRowIssue(
                    row_number=1,
                    code=ImportErrorCode.INVALID_VALUE.value,
                    message=f"Malformed CSV: {exc}",
                ),
            ),
            row_number=1,
        ) from exc

    raw_headers = _prepare_headers(header_cells)
    mapping = map_headers(raw_headers)
    if not mapping.is_valid:
        raise CsvParseError(
            "CSV headers are invalid or incomplete",
            issues=mapping.issues,
            row_number=1,
        )

    # Header consumed above is spreadsheet row 1; data starts at 2.
    last_completed_row = 1
    try:
        for cells in reader:
            row_number = last_completed_row + 1
            stripped_cells = [cell.strip() for cell in cells]
            if not any(stripped_cells):
                last_completed_row = row_number
                continue

            raw_cells: dict[str, str] = {}
            for index, header in enumerate(raw_headers):
                if not header:
                    continue
                raw_cells[header] = (
                    stripped_cells[index] if index < len(stripped_cells) else ""
                )
            yield map_row_values(row_number, raw_cells, mapping)
            last_completed_row = row_number
    except csv.Error as exc:
        # Error is raised while reading the next physical row (before body runs).
        failed_row = last_completed_row + 1
        raise CsvParseError(
            f"Malformed CSV at row {failed_row}: {exc}",
            issues=(
                ImportRowIssue(
                    row_number=failed_row,
                    code=ImportErrorCode.INVALID_VALUE.value,
                    message=f"Malformed CSV: {exc}",
                ),
            ),
            row_number=failed_row,
        ) from exc
