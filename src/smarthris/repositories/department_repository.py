"""Data access for departments."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from smarthris.models.department import Department


class DepartmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, department_id: int) -> Department | None:
        return self._session.get(Department, department_id)

    def create(self, department_name: str, description: str | None = None) -> Department:
        department = Department(department_name=department_name, description=description)
        self._session.add(department)
        self._session.flush()
        return department
