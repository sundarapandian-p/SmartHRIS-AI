"""Employee CRUD and organizational relationship routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.models.employee import EmployeeStatus
from smarthris.schemas.employee import (
    DirectReportsResponse,
    EmployeeCreate,
    EmployeeHierarchyResponse,
    EmployeeListResponse,
    EmployeeRead,
    EmployeeSortField,
    EmployeeUpdate,
    ManagerResponse,
    SortOrder,
)
from smarthris.services.employee_service import EmployeeService

router = APIRouter(prefix="/employees", tags=["employees"])


def get_employee_service(db: Session = Depends(get_db)) -> EmployeeService:
    return EmployeeService(db)


@router.post("", response_model=EmployeeRead, status_code=201)
def create_employee(
    payload: EmployeeCreate,
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeRead:
    return service.create(payload)


@router.get("", response_model=EmployeeListResponse)
def list_employees(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    employee_number: str | None = Query(None, description="Partial match on employee number"),
    name: str | None = Query(None, description="Partial match on first or last name"),
    first_name: str | None = Query(None, description="Partial match on first name"),
    last_name: str | None = Query(None, description="Partial match on last name"),
    work_email: str | None = Query(None, description="Partial match on work email"),
    department_id: int | None = Query(None, ge=1),
    status: EmployeeStatus | None = None,
    sort_by: EmployeeSortField = Query(
        EmployeeSortField.ID,
        description="Sort field (allowlisted)",
    ),
    sort_order: SortOrder = Query(SortOrder.ASC, description="Sort direction"),
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeListResponse:
    return service.list(
        page=page,
        page_size=page_size,
        employee_number=employee_number,
        name=name,
        first_name=first_name,
        last_name=last_name,
        work_email=work_email,
        department_id=department_id,
        status=status,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/{employee_id}/manager", response_model=ManagerResponse)
def get_employee_manager(
    employee_id: int,
    service: EmployeeService = Depends(get_employee_service),
) -> ManagerResponse:
    return service.get_manager(employee_id)


@router.get("/{employee_id}/direct-reports", response_model=DirectReportsResponse)
def get_employee_direct_reports(
    employee_id: int,
    service: EmployeeService = Depends(get_employee_service),
) -> DirectReportsResponse:
    return service.get_direct_reports(employee_id)


@router.get("/{employee_id}/reporting-hierarchy", response_model=EmployeeHierarchyResponse)
def get_employee_reporting_hierarchy(
    employee_id: int,
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeHierarchyResponse:
    return service.get_reporting_hierarchy(employee_id)


@router.get("/{employee_id}", response_model=EmployeeRead)
def get_employee(
    employee_id: int,
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeRead:
    return service.get(employee_id)


@router.patch("/{employee_id}", response_model=EmployeeRead)
def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeRead:
    return service.update(employee_id, payload)
