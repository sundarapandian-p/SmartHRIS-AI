"""Shared types for bulk employee import."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """
    One data row after header mapping.

    ``values`` uses canonical column names (for example ``employee_number``).
    """

    row_number: int
    values: dict[str, str]


@dataclass(frozen=True, slots=True)
class ImportRowIssue:
    """One validation or mapping problem tied to a spreadsheet row."""

    row_number: int
    code: str
    message: str
    field: str | None = None
    employee_number: str | None = None
