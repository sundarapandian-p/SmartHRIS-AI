"""Pydantic model for one enrolment row in a bulk import file."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from smarthris.importing.enrolment_column_mapping import enrolment_record_key
from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.models.course_enrolment import CourseEnrolmentStatus


def _clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class EnrolmentImportRow(BaseModel):
    """Validated enrolment CSV row before DB resolve/insert."""

    employee_number: str = Field(min_length=1, max_length=50)
    course_code: str = Field(min_length=1, max_length=50)
    enrolment_date: date
    status: CourseEnrolmentStatus
    completion_date: date | None = None
    score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)

    @field_validator("employee_number", "course_code", mode="before")
    @classmethod
    def strip_required_strings(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator("enrolment_date", "completion_date", mode="before")
    @classmethod
    def parse_date(cls, value: Any) -> Any:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            return None
        if isinstance(value, date) and not isinstance(value, str):
            return value
        try:
            return date.fromisoformat(cleaned)
        except ValueError as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_DATE.value,
                "date must be ISO format YYYY-MM-DD",
                {"field": "date"},
            ) from exc

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, value: Any) -> Any:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            raise PydanticCustomError(
                ImportErrorCode.MISSING_REQUIRED.value,
                "status is required",
                {"field": "status"},
            )
        if isinstance(value, CourseEnrolmentStatus):
            return value
        try:
            return CourseEnrolmentStatus(cleaned.lower())
        except ValueError as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_STATUS.value,
                "status must be enrolled, in_progress, completed, or cancelled",
                {"field": "status"},
            ) from exc

    @field_validator("score", mode="before")
    @classmethod
    def parse_score(cls, value: Any) -> Any:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            return None
        try:
            return Decimal(cleaned)
        except (InvalidOperation, ValueError) as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_VALUE.value,
                "score must be a decimal between 0 and 100",
                {"field": "score"},
            ) from exc

    @model_validator(mode="after")
    def validate_completion_rules(self) -> EnrolmentImportRow:
        if self.enrolment_date is None:
            raise PydanticCustomError(
                ImportErrorCode.MISSING_REQUIRED.value,
                "enrolment_date is required",
                {"field": "enrolment_date"},
            )
        if self.completion_date is not None and self.completion_date < self.enrolment_date:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_DATE.value,
                "completion_date must not precede enrolment_date",
                {"field": "completion_date"},
            )
        if (
            self.status is CourseEnrolmentStatus.COMPLETED
            and self.completion_date is None
        ):
            raise PydanticCustomError(
                ImportErrorCode.MISSING_REQUIRED.value,
                "completed enrolments require a completion_date",
                {"field": "completion_date"},
            )
        return self


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
    employee_number: str | None,
    course_code: str | None,
) -> list[ImportRowIssue]:
    record_key = None
    if employee_number and course_code:
        record_key = enrolment_record_key(employee_number, course_code)
    elif employee_number or course_code:
        record_key = f"{employee_number or ''}|{course_code or ''}"

    issues: list[ImportRowIssue] = []
    for err in exc.errors():
        field = _field_from_error(err)
        msg = err.get("msg", "Invalid value")
        err_type = str(err.get("type", ""))

        if err_type == ImportErrorCode.MISSING_REQUIRED.value:
            code = ImportErrorCode.MISSING_REQUIRED.value
        elif err_type == ImportErrorCode.INVALID_STATUS.value:
            code = ImportErrorCode.INVALID_STATUS.value
        elif err_type == ImportErrorCode.INVALID_DATE.value:
            code = ImportErrorCode.INVALID_DATE.value
        elif err_type == ImportErrorCode.INVALID_VALUE.value:
            code = ImportErrorCode.INVALID_VALUE.value
        elif err_type in {"string_too_short", "missing"}:
            code = ImportErrorCode.MISSING_REQUIRED.value
        elif field == "enrolment_date" and "required" in msg.lower():
            code = ImportErrorCode.MISSING_REQUIRED.value
        else:
            code = _coerce_code(err_type)

        issues.append(
            ImportRowIssue(
                row_number=row_number,
                code=code,
                message=msg,
                field=field,
                employee_number=employee_number,
                record_key=record_key,
            )
        )
    return issues


def parse_enrolment_import_row(
    row_number: int,
    values: dict[str, str],
) -> tuple[EnrolmentImportRow | None, list[ImportRowIssue]]:
    employee_number = _clean_optional_str(values.get("employee_number"))
    course_code = _clean_optional_str(values.get("course_code"))

    payload: dict[str, Any] = {}
    for key, raw in values.items():
        payload[key] = raw.strip() if isinstance(raw, str) else raw

    try:
        row = EnrolmentImportRow.model_validate(payload)
        return row, []
    except ValidationError as exc:
        return None, _validation_error_to_issues(
            row_number, exc, employee_number, course_code
        )
