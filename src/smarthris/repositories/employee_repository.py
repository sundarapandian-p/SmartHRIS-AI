"""Data access for employees."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from smarthris.config import get_settings
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.schemas.employee import EmployeeSortField, SortOrder


class EmployeeFilters:
    """Optional filters for listing employees."""

    def __init__(
        self,
        *,
        employee_number: str | None = None,
        name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        work_email: str | None = None,
        department_id: int | None = None,
        status: EmployeeStatus | None = None,
    ) -> None:
        self.employee_number = employee_number
        self.name = name
        self.first_name = first_name
        self.last_name = last_name
        self.work_email = work_email
        self.department_id = department_id
        self.status = status


_SORT_COLUMNS = {
    EmployeeSortField.ID: Employee.id,
    EmployeeSortField.EMPLOYEE_NUMBER: Employee.employee_number,
    EmployeeSortField.FIRST_NAME: Employee.first_name,
    EmployeeSortField.LAST_NAME: Employee.last_name,
    EmployeeSortField.WORK_EMAIL: Employee.work_email,
    EmployeeSortField.HIRE_DATE: Employee.hire_date,
    EmployeeSortField.STATUS: Employee.status,
    EmployeeSortField.DEPARTMENT_ID: Employee.department_id,
    EmployeeSortField.CREATED_AT: Employee.created_at,
}


@dataclass(frozen=True)
class DescendantRow:
    """Employee row with depth below a hierarchy root (1 = direct report)."""

    employee: Employee
    depth: int


def _unique_trimmed(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _unique_lower_emails(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        value = raw.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


class EmployeeRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, employee_id: int) -> Employee | None:
        return self._session.get(Employee, employee_id)

    def get_manager_id(self, employee_id: int) -> int | None:
        employee = self._session.get(Employee, employee_id)
        if employee is None:
            return None
        return employee.manager_employee_id

    def add(self, employee: Employee) -> Employee:
        self._session.add(employee)
        return employee

    def add_many(self, employees: Sequence[Employee]) -> None:
        """Stage multiple employees for insert (caller flushes/commits)."""
        if not employees:
            return
        self._session.add_all(list(employees))

    def find_existing_employee_numbers(self, numbers: Sequence[str]) -> set[str]:
        """
        Return employee_number values that already exist in the database.

        Queries in chunks of ``import_batch_size``; does not load Employee ORM rows.
        """
        return set(self.map_employee_numbers_to_ids(numbers))

    def map_employee_numbers_to_ids(self, numbers: Sequence[str]) -> dict[str, int]:
        """
        Return ``employee_number → id`` for numbers that exist (chunked lookups).
        """
        unique_numbers = _unique_trimmed(numbers)
        if not unique_numbers:
            return {}

        batch_size = get_settings().import_batch_size
        mapping: dict[str, int] = {}
        for start in range(0, len(unique_numbers), batch_size):
            chunk = unique_numbers[start : start + batch_size]
            rows = self._session.execute(
                select(Employee.employee_number, Employee.id).where(
                    Employee.employee_number.in_(chunk)
                )
            ).all()
            for employee_number, employee_id in rows:
                mapping[str(employee_number)] = int(employee_id)
        return mapping

    def find_existing_work_emails(self, emails: Sequence[str]) -> set[str]:
        """
        Return work_email values that already exist (compared lowercase).

        Queries in chunks of ``import_batch_size``; does not load Employee ORM rows.
        """
        unique_emails = _unique_lower_emails(emails)
        if not unique_emails:
            return set()

        batch_size = get_settings().import_batch_size
        found: set[str] = set()
        for start in range(0, len(unique_emails), batch_size):
            chunk = unique_emails[start : start + batch_size]
            rows = self._session.scalars(
                select(Employee.work_email).where(Employee.work_email.in_(chunk))
            ).all()
            found.update(str(email).lower() for email in rows)
        return found

    def _apply_filters(self, query, filters: EmployeeFilters):
        if filters.employee_number:
            query = query.where(Employee.employee_number.like(f"%{filters.employee_number}%"))
        if filters.name:
            term = f"%{filters.name}%"
            query = query.where(
                or_(
                    Employee.first_name.like(term),
                    Employee.last_name.like(term),
                )
            )
        if filters.first_name:
            query = query.where(Employee.first_name.like(f"%{filters.first_name}%"))
        if filters.last_name:
            query = query.where(Employee.last_name.like(f"%{filters.last_name}%"))
        if filters.work_email:
            query = query.where(Employee.work_email.like(f"%{filters.work_email}%"))
        if filters.department_id is not None:
            query = query.where(Employee.department_id == filters.department_id)
        if filters.status is not None:
            query = query.where(Employee.status == filters.status)
        return query

    @staticmethod
    def _apply_sort(
        query,
        sort_by: EmployeeSortField,
        sort_order: SortOrder,
    ):
        column = _SORT_COLUMNS[sort_by]
        if sort_order == SortOrder.ASC:
            return query.order_by(column.asc())
        return query.order_by(column.desc())

    def count(self, filters: EmployeeFilters) -> int:
        query = select(func.count()).select_from(Employee)
        query = self._apply_filters(query, filters)
        return int(self._session.scalar(query) or 0)

    def count_direct_reports(self, manager_employee_id: int) -> int:
        query = select(func.count()).select_from(Employee).where(
            Employee.manager_employee_id == manager_employee_id
        )
        return int(self._session.scalar(query) or 0)

    def list_page(
        self,
        filters: EmployeeFilters,
        *,
        offset: int,
        limit: int,
        sort_by: EmployeeSortField = EmployeeSortField.ID,
        sort_order: SortOrder = SortOrder.ASC,
    ) -> list[Employee]:
        query = select(Employee)
        query = self._apply_filters(query, filters)
        query = self._apply_sort(query, sort_by, sort_order)
        query = query.offset(offset).limit(limit)
        return list(self._session.scalars(query).all())

    def list_direct_reports(
        self,
        manager_employee_id: int,
        *,
        sort_by: EmployeeSortField = EmployeeSortField.ID,
        sort_order: SortOrder = SortOrder.ASC,
    ) -> list[Employee]:
        query = select(Employee).where(Employee.manager_employee_id == manager_employee_id)
        query = self._apply_sort(query, sort_by, sort_order)
        return list(self._session.scalars(query).all())

    def fetch_descendants(
        self,
        root_employee_id: int,
        *,
        max_depth: int,
    ) -> list[DescendantRow]:
        """
        All employees in the reporting subtree below root (excluding root), with depth.
        Uses a single recursive CTE query (MySQL 8+).
        """
        sql = text(
            """
            WITH RECURSIVE sub AS (
                SELECT id, 1 AS depth
                FROM employees
                WHERE manager_employee_id = :root_id
                UNION ALL
                SELECT e.id, s.depth + 1
                FROM employees e
                INNER JOIN sub s ON e.manager_employee_id = s.id
                WHERE s.depth < :max_depth
            )
            SELECT e.id AS employee_id, sub.depth AS depth
            FROM sub
            INNER JOIN employees e ON e.id = sub.id
            ORDER BY sub.depth, e.id
            """
        )
        rows = self._session.execute(
            sql,
            {"root_id": root_employee_id, "max_depth": max_depth},
        ).all()
        if not rows:
            return []

        employee_ids = {int(row.employee_id) for row in rows}
        employees = list(
            self._session.scalars(
                select(Employee).where(Employee.id.in_(employee_ids))
            ).all()
        )
        employees_by_id = {emp.id: emp for emp in employees}
        return [
            DescendantRow(
                employee=employees_by_id[int(row.employee_id)],
                depth=int(row.depth),
            )
            for row in rows
            if int(row.employee_id) in employees_by_id
        ]
