"""Pydantic model for one employee row in a bulk import file."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, EmailStr, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.models.employee import EmployeeStatus


def _clean_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


class EmployeeImportRow(BaseModel):
    """Validated employee row from CSV/XLSX before database checks."""

    employee_number: str = Field(min_length=1, max_length=50)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    work_email: EmailStr
    department_id: int | None = Field(default=None, gt=0)
    department_name: str | None = Field(default=None, max_length=120)
    job_role: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=150)
    manager_employee_number: str | None = Field(default=None, max_length=50)
    hire_date: date | None = None
    status: EmployeeStatus = EmployeeStatus.ACTIVE

    @field_validator("employee_number", "first_name", "last_name", mode="before")
    @classmethod
    def strip_required_strings(cls, value: Any) -> Any:
        if value is None:
            return value
        return str(value).strip()

    @field_validator(
        "department_name",
        "job_role",
        "job_title",
        "manager_employee_number",
        mode="before",
    )
    @classmethod
    def strip_optional_strings(cls, value: Any) -> str | None:
        return _clean_optional_str(value)

    @field_validator("department_id", mode="before")
    @classmethod
    def parse_department_id(cls, value: Any) -> int | None:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            return None
        try:
            parsed = int(cleaned)
        except ValueError as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_VALUE.value,
                "department_id must be a positive integer",
                {"field": "department_id"},
            ) from exc
        if parsed <= 0:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_VALUE.value,
                "department_id must be a positive integer",
                {"field": "department_id"},
            )
        return parsed

    @field_validator("work_email", mode="before")
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            raise PydanticCustomError(
                ImportErrorCode.MISSING_REQUIRED.value,
                "work_email is required",
                {"field": "work_email"},
            )
        return cleaned.lower()

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, value: Any) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return EmployeeStatus.ACTIVE
        if isinstance(value, EmployeeStatus):
            return value
        text = str(value).strip().lower()
        try:
            return EmployeeStatus(text)
        except ValueError as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_STATUS.value,
                "status must be 'active' or 'inactive'",
                {"field": "status"},
            ) from exc

    @field_validator("hire_date", mode="before")
    @classmethod
    def parse_hire_date(cls, value: Any) -> date | None:
        cleaned = _clean_optional_str(value)
        if cleaned is None:
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(cleaned)
        except ValueError as exc:
            raise PydanticCustomError(
                ImportErrorCode.INVALID_DATE.value,
                "hire_date must be ISO format YYYY-MM-DD",
                {"field": "hire_date"},
            ) from exc

    @model_validator(mode="after")
    def require_department_reference(self) -> EmployeeImportRow:
        if self.department_id is None and self.department_name is None:
            raise PydanticCustomError(
                ImportErrorCode.MISSING_REQUIRED.value,
                "Provide department_id and/or department_name",
                {"field": "department"},
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
        elif err_type == ImportErrorCode.INVALID_DATE.value:
            code = ImportErrorCode.INVALID_DATE.value
        elif err_type == ImportErrorCode.INVALID_VALUE.value:
            code = ImportErrorCode.INVALID_VALUE.value
        elif err_type in {"string_too_short", "missing"} or (
            field in {"employee_number", "first_name", "last_name"}
            and "at least 1 character" in msg.lower()
        ):
            code = ImportErrorCode.MISSING_REQUIRED.value
        elif field == "work_email" and (
            "valid email" in msg.lower() or err_type.startswith("value_error")
        ):
            code = ImportErrorCode.INVALID_EMAIL.value
        else:
            code = _coerce_code(err_type)

        issues.append(
            ImportRowIssue(
                row_number=row_number,
                code=code,
                message=msg,
                field=field,
                employee_number=employee_number,
            )
        )
    return issues


def parse_employee_import_row(
    row_number: int,
    values: dict[str, str],
) -> tuple[EmployeeImportRow | None, list[ImportRowIssue]]:
    """Parse canonical string cells into an EmployeeImportRow."""
    employee_number = _clean_optional_str(values.get("employee_number"))

    payload: dict[str, Any] = {}
    for key, raw in values.items():
        payload[key] = raw.strip() if isinstance(raw, str) else raw

    try:
        row = EmployeeImportRow.model_validate(payload)
        return row, []
    except ValidationError as exc:
        return None, _validation_error_to_issues(row_number, exc, employee_number)
