"""Pydantic model for one course row in a bulk import file."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.models.course import CourseStatus


def _clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class CourseImportRow(BaseModel):
    """Validated course row from CSV before database checks."""

    course_code: str = Field(min_length=1, max_length=50)
    course_name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    description: str | None = None
    duration_hours: Decimal = Field(gt=0, max_digits=6, decimal_places=2)
    status: CourseStatus = CourseStatus.ACTIVE

    @field_validator("course_code", "course_name", "category", mode="before")
    @classmethod
    def strip_required_strings(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("description", mode="before")
    @classmethod
    def strip_optional_description(cls, value: Any) -> str | None:
        return _clean_optional_str(value)

    @field_validator("duration_hours", mode="before")
    @classmethod
    def parse_duration_hours(cls, value: Any) -> Any:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            raise PydanticCustomError(
                ImportErrorCode.MISSING_REQUIRED.value,
                "duration_hours is required",
                {"field": "duration_hours"},
            )
        try:
            parsed = Decimal(cleaned)
        except (InvalidOperation, ValueError) as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_VALUE.value,
                "duration_hours must be a positive decimal number",
                {"field": "duration_hours"},
            ) from exc
        if parsed <= 0:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_VALUE.value,
                "duration_hours must be greater than zero",
                {"field": "duration_hours"},
            )
        return parsed

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return CourseStatus.ACTIVE
        if isinstance(value, CourseStatus):
            return value
        text = str(value).strip().lower()
        try:
            return CourseStatus(text)
        except ValueError as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_STATUS.value,
                "status must be 'active' or 'inactive'",
                {"field": "status"},
            ) from exc


def _coerce_code(raw: str) -> str:
    try:
        return ImportErrorCode(raw).value
    except ValueError:
        return ImportErrorCode.INVALID_VALUE.value


def _field_from_error(err: dict[str, Any]) -> str | None:
    loc = err.get("loc", ())
    if loc:
        return str(loc[0])
    ctx = err.get("ctx")
    if isinstance(ctx, dict) and isinstance(ctx.get("field"), str):
        return ctx["field"]
    return None


def _validation_error_to_issues(
    row_number: int,
    exc: ValidationError,
    course_code: str | None,
) -> list[ImportRowIssue]:
    issues: list[ImportRowIssue] = []
    for err in exc.errors():
        field = _field_from_error(err)
        msg = err.get("msg", "Invalid value")
        err_type = str(err.get("type", ""))

        if err_type == ImportErrorCode.MISSING_REQUIRED.value:
            code = ImportErrorCode.MISSING_REQUIRED.value
        elif err_type == ImportErrorCode.INVALID_STATUS.value:
            code = ImportErrorCode.INVALID_STATUS.value
        elif err_type == ImportErrorCode.INVALID_VALUE.value:
            code = ImportErrorCode.INVALID_VALUE.value
        elif err_type in {"string_too_short", "missing"} or (
            field in {"course_code", "course_name", "category"}
            and "at least 1 character" in msg.lower()
        ):
            code = ImportErrorCode.MISSING_REQUIRED.value
        elif "decimal" in err_type or "greater" in msg.lower() or field == "duration_hours":
            code = ImportErrorCode.INVALID_VALUE.value
        else:
            code = _coerce_code(err_type)

        issues.append(
            ImportRowIssue(
                row_number=row_number,
                code=code,
                message=msg,
                field=field,
                record_key=course_code,
            )
        )
    return issues


def parse_course_import_row(
    row_number: int,
    values: dict[str, str],
) -> tuple[CourseImportRow | None, list[ImportRowIssue]]:
    """Parse canonical string cells into a CourseImportRow."""
    course_code = _clean_optional_str(values.get("course_code"))

    payload: dict[str, Any] = {}
    for key, raw in values.items():
        payload[key] = raw.strip() if isinstance(raw, str) else raw

    try:
        row = CourseImportRow.model_validate(payload)
        return row, []
    except ValidationError as exc:
        return None, _validation_error_to_issues(row_number, exc, course_code)
