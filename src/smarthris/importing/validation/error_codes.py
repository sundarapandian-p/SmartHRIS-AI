"""Stable error codes stored on import_errors.code."""

from enum import StrEnum


class ImportErrorCode(StrEnum):
    MISSING_REQUIRED = "missing_required"
    INVALID_EMAIL = "invalid_email"
    INVALID_STATUS = "invalid_status"
    INVALID_DATE = "invalid_date"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    ALREADY_EXISTS = "already_exists"
    UNKNOWN_DEPARTMENT = "unknown_department"
    DEPARTMENT_MISMATCH = "department_mismatch"
    UNKNOWN_EMPLOYEE = "unknown_employee"
    UNKNOWN_COURSE = "unknown_course"
    INVALID_VALUE = "invalid_value"
