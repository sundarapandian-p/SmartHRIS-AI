"""Validation rules for application user accounts (provisioning and updates)."""

from smarthris.core.exceptions import ValidationError
from smarthris.models.user import UserRole


def validate_user_employee_link(
    *,
    role: UserRole,
    employee_id: int | None,
) -> None:
    """Enforce employee_id rules by role.

  - ``employee`` requires a linked ``employee_id``.
  - ``admin`` and ``hr`` may omit ``employee_id``.
  """
    if role == UserRole.EMPLOYEE and employee_id is None:
        raise ValidationError("Employee role requires a linked employee_id")
    if role == UserRole.EMPLOYEE and employee_id is not None and employee_id <= 0:
        raise ValidationError("employee_id must be a positive integer")