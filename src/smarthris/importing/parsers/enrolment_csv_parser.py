"""Streaming CSV parser for LMS enrolment bulk import."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

from smarthris.importing.enrolment_column_mapping import (
    map_enrolment_headers,
    map_enrolment_row_values,
)
from smarthris.importing.parsers.csv_parser import CsvParseError  # registers smarthris_csv
from smarthris.importing.types import ImportRowIssue, ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode

_BOM = "\ufeff"


def parse_enrolment_csv(source: str | Path | TextIO) -> Iterator[ParsedRow]:
    """Stream ParsedRow values using enrolment header mapping only."""
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
    mapping = map_enrolment_headers(raw_headers)
    if not mapping.is_valid:
        raise CsvParseError(
            "CSV headers are invalid or incomplete",
            issues=mapping.issues,
            row_number=1,
        )

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
            yield map_enrolment_row_values(row_number, raw_cells, mapping)
            last_completed_row = row_number
    except csv.Error as exc:
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
