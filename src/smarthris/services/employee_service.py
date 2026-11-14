"""Employee business logic and transactions."""

import math
from collections import defaultdict

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smarthris.core.constants import (
    DIRECT_REPORTS_MAX,
    HIERARCHY_MAX_DEPTH,
    HIERARCHY_MAX_NODES,
    MANAGER_CHAIN_VALIDATION_MAX,
)
from smarthris.core.access import assert_can_manage_employees, assert_can_read_employee
from smarthris.core.exceptions import ConflictError, NotFoundError, ValidationError
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.models.user import User
from smarthris.repositories.department_repository import DepartmentRepository
from smarthris.repositories.employee_repository import EmployeeFilters, EmployeeRepository
from smarthris.schemas.employee import (
    DirectReportsResponse,
    EmployeeCreate,
    EmployeeHierarchyNode,
    EmployeeHierarchyResponse,
    EmployeeListResponse,
    EmployeeRead,
    EmployeeSortField,
    EmployeeUpdate,
    ManagerResponse,
    SortOrder,
)


class EmployeeService:
    def __init__(self, session: Session, *, actor: User) -> None:
        self._session = session
        self._actor = actor
        self._employees = EmployeeRepository(session)
        self._departments = DepartmentRepository(session)

    def create(self, payload: EmployeeCreate) -> EmployeeRead:
        assert_can_manage_employees(self._actor)
        self._ensure_department_exists(payload.department_id)

        employee = Employee(
            employee_number=payload.employee_number.strip(),
            first_name=payload.first_name.strip(),
            last_name=payload.last_name.strip(),
            work_email=str(payload.work_email).lower(),
            department_id=payload.department_id,
            job_role=payload.job_role,
            job_title=payload.job_title,
            manager_employee_id=None,
            hire_date=payload.hire_date,
            status=payload.status,
        )
        self._employees.add(employee)
        self._session.flush()

        if payload.manager_employee_id is not None:
            self._validate_manager_assignment(employee.id, payload.manager_employee_id)
            employee.manager_employee_id = payload.manager_employee_id

        self._commit_or_raise_conflict()
        self._session.refresh(employee)
        return EmployeeRead.model_validate(employee)

    def get(self, employee_id: int) -> EmployeeRead:
        assert_can_read_employee(self._actor, employee_id)
        employee = self._employees.get_by_id(employee_id)
        if employee is None:
            raise NotFoundError("Employee not found")
        return EmployeeRead.model_validate(employee)

    def get_manager(self, employee_id: int) -> ManagerResponse:
        assert_can_read_employee(self._actor, employee_id)
        employee = self._employees.get_by_id(employee_id)
        if employee is None:
            raise NotFoundError("Employee not found")
        if employee.manager_employee_id is None:
            return ManagerResponse(manager=None)
        manager = self._employees.get_by_id(employee.manager_employee_id)
        if manager is None:
            return ManagerResponse(manager=None)
        return ManagerResponse(manager=EmployeeRead.model_validate(manager))

    def get_direct_reports(self, employee_id: int) -> DirectReportsResponse:
        assert_can_manage_employees(self._actor)
        if self._employees.get_by_id(employee_id) is None:
            raise NotFoundError("Employee not found")

        total = self._employees.count_direct_reports(employee_id)
        if total > DIRECT_REPORTS_MAX:
            raise ValidationError(
                f"Direct reports exceed the maximum of {DIRECT_REPORTS_MAX} employees. "
                "Use filters or organizational views in a future release."
            )

        rows = self._employees.list_direct_reports(employee_id)
        return DirectReportsResponse(
            employee_id=employee_id,
            total=total,
            items=[EmployeeRead.model_validate(row) for row in rows],
        )

    def get_reporting_hierarchy(self, employee_id: int) -> EmployeeHierarchyResponse:
        assert_can_manage_employees(self._actor)
        if self._employees.get_by_id(employee_id) is None:
            raise NotFoundError("Employee not found")

        # Fetch one level deeper than allowed to detect truncated/incomplete subtrees.
        probe_rows = self._employees.fetch_descendants(
            employee_id,
            max_depth=HIERARCHY_MAX_DEPTH + 1,
        )
        probe_ids = [row.employee.id for row in probe_rows]
        if len(probe_ids) != len(set(probe_ids)):
            raise ValidationError("Invalid reporting hierarchy data: cycle detected in manager chain")

        if any(row.depth > HIERARCHY_MAX_DEPTH for row in probe_rows):
            raise ValidationError(
                f"Reporting hierarchy exceeds the maximum depth of {HIERARCHY_MAX_DEPTH} levels."
            )

        descendants = [row for row in probe_rows if row.depth <= HIERARCHY_MAX_DEPTH]

        if len(descendants) > HIERARCHY_MAX_NODES:
            raise ValidationError(
                f"Reporting hierarchy exceeds the maximum of {HIERARCHY_MAX_NODES} employees. "
                "The subtree is too large to return in one response."
            )

        children_by_manager: dict[int, list[Employee]] = defaultdict(list)
        for row in descendants:
            children_by_manager[row.employee.manager_employee_id].append(row.employee)

        def build_nodes(parent_id: int, *, level: int = 0) -> list[EmployeeHierarchyNode]:
            if level > HIERARCHY_MAX_DEPTH:
                raise ValidationError(
                    "Invalid reporting hierarchy data: cycle or excessive depth detected"
                )
            nodes: list[EmployeeHierarchyNode] = []
            for emp in children_by_manager.get(parent_id, []):
                nodes.append(
                    EmployeeHierarchyNode(
                        employee=EmployeeRead.model_validate(emp),
                        direct_reports=build_nodes(emp.id, level=level + 1),
                    )
                )
            return nodes

        return EmployeeHierarchyResponse(
            employee_id=employee_id,
            direct_reports=build_nodes(employee_id),
        )

    def list(
        self,
        *,
        page: int,
        page_size: int,
        employee_number: str | None = None,
        name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        work_email: str | None = None,
        department_id: int | None = None,
        status: EmployeeStatus | None = None,
        sort_by: EmployeeSortField = EmployeeSortField.ID,
        sort_order: SortOrder = SortOrder.ASC,
    ) -> EmployeeListResponse:
        assert_can_manage_employees(self._actor)
        if page < 1:
            raise ValidationError("page must be >= 1")
        if page_size < 1 or page_size > 100:
            raise ValidationError("page_size must be between 1 and 100")

        filters = EmployeeFilters(
            employee_number=employee_number,
            name=name,
            first_name=first_name,
            last_name=last_name,
            work_email=work_email,
            department_id=department_id,
            status=status,
        )
        total = self._employees.count(filters)
        offset = (page - 1) * page_size
        rows = self._employees.list_page(
            filters,
            offset=offset,
            limit=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        pages = math.ceil(total / page_size) if total else 0
        return EmployeeListResponse(
            items=[EmployeeRead.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def update(self, employee_id: int, payload: EmployeeUpdate) -> EmployeeRead:
        assert_can_manage_employees(self._actor)
        employee = self._employees.get_by_id(employee_id)
        if employee is None:
            raise NotFoundError("Employee not found")

        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            return EmployeeRead.model_validate(employee)

        if "department_id" in updates and updates["department_id"] is not None:
            self._ensure_department_exists(updates["department_id"])

        if "manager_employee_id" in updates:
            self._validate_manager_assignment(employee_id, updates["manager_employee_id"])

        if "work_email" in updates and updates["work_email"] is not None:
            updates["work_email"] = str(updates["work_email"]).lower()
        if "employee_number" in updates and updates["employee_number"] is not None:
            updates["employee_number"] = updates["employee_number"].strip()
        if "first_name" in updates and updates["first_name"] is not None:
            updates["first_name"] = updates["first_name"].strip()
        if "last_name" in updates and updates["last_name"] is not None:
            updates["last_name"] = updates["last_name"].strip()

        for field, value in updates.items():
            setattr(employee, field, value)

        self._commit_or_raise_conflict()
        self._session.refresh(employee)
        return EmployeeRead.model_validate(employee)

    def _validate_manager_assignment(
        self,
        employee_id: int,
        manager_id: int | None,
    ) -> None:
        if manager_id is None:
            return
        if manager_id == employee_id:
            raise ValidationError("An employee cannot be their own manager")
        self._ensure_manager_exists(manager_id)

        current: int | None = manager_id
        steps = 0
        while current is not None and steps < MANAGER_CHAIN_VALIDATION_MAX:
            if current == employee_id:
                raise ValidationError("Manager assignment would create a reporting cycle")
            manager = self._employees.get_by_id(current)
            if manager is None:
                break
            current = manager.manager_employee_id
            steps += 1

        if steps >= MANAGER_CHAIN_VALIDATION_MAX and current is not None:
            raise ValidationError(
                "Unable to validate manager assignment: reporting chain is too deep"
            )

    def _ensure_department_exists(self, department_id: int) -> None:
        if self._departments.get_by_id(department_id) is None:
            raise NotFoundError("Department not found")

    def _ensure_manager_exists(self, manager_id: int) -> None:
        if self._employees.get_by_id(manager_id) is None:
            raise NotFoundError("Manager employee not found")

    def _commit_or_raise_conflict(self) -> None:
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raise ConflictError(
                "Employee number or work email already exists, or a related record is invalid"
            ) from None
