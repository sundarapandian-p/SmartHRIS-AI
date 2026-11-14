"""Unit tests for user role / employee_id validation (no database)."""

import pytest

from smarthris.core.exceptions import ValidationError
from smarthris.core.user_validation import validate_user_employee_link
from smarthris.models.user import UserRole


def test_admin_may_have_null_employee_id() -> None:
    validate_user_employee_link(role=UserRole.ADMIN, employee_id=None)


def test_hr_may_have_null_employee_id() -> None:
    validate_user_employee_link(role=UserRole.HR, employee_id=None)


def test_admin_may_link_employee() -> None:
    validate_user_employee_link(role=UserRole.ADMIN, employee_id=10)


def test_employee_requires_employee_id() -> None:
    with pytest.raises(ValidationError, match="employee_id"):
        validate_user_employee_link(role=UserRole.EMPLOYEE, employee_id=None)


def test_employee_requires_positive_employee_id() -> None:
    with pytest.raises(ValidationError, match="positive"):
        validate_user_employee_link(role=UserRole.EMPLOYEE, employee_id=0)


def test_employee_role_with_employee_id_ok() -> None:
    validate_user_employee_link(role=UserRole.EMPLOYEE, employee_id=42)