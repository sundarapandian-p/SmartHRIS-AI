"""Role and document visibility access helpers (database-backed roles only)."""

from __future__ import annotations

from smarthris.core.exceptions import ForbiddenError, NotFoundError
from smarthris.models.hr_document import HrDocumentVisibility
from smarthris.models.user import User, UserRole

_FORBIDDEN = "You do not have permission to perform this action"


def is_hr_or_admin(user: User) -> bool:
    return user.role in {UserRole.ADMIN, UserRole.HR}


def is_employee(user: User) -> bool:
    return user.role == UserRole.EMPLOYEE


def assert_employee_linked(user: User) -> None:
    if is_employee(user) and user.employee_id is None:
        raise ForbiddenError("Employee account is not linked to an employee record")


def assert_can_manage_employees(user: User) -> None:
    if not is_hr_or_admin(user):
        raise ForbiddenError(_FORBIDDEN)


def assert_can_read_employee(user: User, employee_id: int) -> None:
    """HR/Admin: any id; Employee: own record only (404 on mismatch)."""
    if is_hr_or_admin(user):
        return
    if is_employee(user):
        assert_employee_linked(user)
        if user.employee_id != employee_id:
            raise NotFoundError("Employee not found")
        return
    raise ForbiddenError(_FORBIDDEN)


def assert_employee_org_views_denied(user: User) -> None:
    """Block employee access to org-wide hierarchy / direct-reports endpoints."""
    if is_employee(user):
        raise ForbiddenError(_FORBIDDEN)


def allowed_document_visibilities(user: User) -> frozenset[HrDocumentVisibility]:
    if is_hr_or_admin(user):
        return frozenset({HrDocumentVisibility.HR_ONLY, HrDocumentVisibility.ALL_STAFF})
    if is_employee(user):
        assert_employee_linked(user)
        return frozenset({HrDocumentVisibility.ALL_STAFF})
    raise ForbiddenError(_FORBIDDEN)