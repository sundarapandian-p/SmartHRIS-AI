"""Data access for departments."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from smarthris.models.department import Department


def normalize_department_name(name: str) -> str:
    """Trim whitespace and lowercase for case-insensitive department matching."""
    return " ".join(name.strip().split()).lower()


class DepartmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, department_id: int) -> Department | None:
        return self._session.get(Department, department_id)

    def get_by_name(self, name: str) -> Department | None:
        """Return a department whose name matches case-insensitively after trim."""
        needle = normalize_department_name(name)
        if not needle:
            return None
        for department in self.list_all():
            if normalize_department_name(department.department_name) == needle:
                return department
        return None

    def list_all(self) -> list[Department]:
        """Load all departments (small table; used once per import validation pass)."""
        return list(self._session.scalars(select(Department).order_by(Department.id)).all())

    def create(self, department_name: str, description: str | None = None) -> Department:
        department = Department(department_name=department_name, description=description)
        self._session.add(department)
        self._session.flush()
        return department
