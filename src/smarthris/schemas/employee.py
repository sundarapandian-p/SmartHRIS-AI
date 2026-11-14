"""Pydantic schemas for employee API contracts."""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from smarthris.models.employee import EmployeeStatus
from smarthris.schemas.app_email import AppEmailStr


class EmployeeSortField(str, Enum):
    """Allowlisted columns for GET /employees sorting."""

    ID = "id"
    EMPLOYEE_NUMBER = "employee_number"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    WORK_EMAIL = "work_email"
    HIRE_DATE = "hire_date"
    STATUS = "status"
    DEPARTMENT_ID = "department_id"
    CREATED_AT = "created_at"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class EmployeeCreate(BaseModel):
    """Body for POST /employees."""

    employee_number: str = Field(min_length=1, max_length=50)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    work_email: AppEmailStr
    department_id: int = Field(gt=0)
    job_role: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=150)
    manager_employee_id: int | None = Field(default=None, gt=0)
    hire_date: date | None = None
    status: EmployeeStatus = EmployeeStatus.ACTIVE


class EmployeeUpdate(BaseModel):
    """Body for PATCH /employees/{id} — only sent fields are updated."""

    employee_number: str | None = Field(default=None, min_length=1, max_length=50)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    work_email: AppEmailStr | None = None
    department_id: int | None = Field(default=None, gt=0)
    job_role: str | None = Field(default=None, max_length=100)
    job_title: str | None = Field(default=None, max_length=150)
    manager_employee_id: int | None = None
    hire_date: date | None = None
    status: EmployeeStatus | None = None


class EmployeeRead(BaseModel):
    """Employee returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_number: str
    first_name: str
    last_name: str
    work_email: AppEmailStr
    department_id: int
    job_role: str | None
    job_title: str | None
    manager_employee_id: int | None
    hire_date: date | None
    status: EmployeeStatus
    created_at: datetime
    updated_at: datetime


class EmployeeListResponse(BaseModel):
    """Paginated employee list."""

    items: list[EmployeeRead]
    total: int
    page: int
    page_size: int
    pages: int


class ManagerResponse(BaseModel):
    """GET /employees/{id}/manager — null when the employee has no manager."""

    manager: EmployeeRead | None = None


class DirectReportsResponse(BaseModel):
    """Unpaginated direct reports (capped at DIRECT_REPORTS_MAX in the service)."""

    employee_id: int
    total: int
    items: list[EmployeeRead]


class EmployeeHierarchyNode(BaseModel):
    """One node in a reporting tree (employee plus nested reports)."""

    employee: EmployeeRead
    direct_reports: list["EmployeeHierarchyNode"] = Field(default_factory=list)


class EmployeeHierarchyResponse(BaseModel):
    """Full reporting hierarchy beneath an employee (root employee is not included)."""

    employee_id: int
    direct_reports: list[EmployeeHierarchyNode] = Field(default_factory=list)


EmployeeHierarchyNode.model_rebuild()
