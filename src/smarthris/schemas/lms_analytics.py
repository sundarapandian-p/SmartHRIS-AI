"""Pydantic schemas for LMS analytics API contracts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from smarthris.models.course import CourseStatus
from smarthris.models.course_enrolment import CourseEnrolmentStatus
from smarthris.models.employee import EmployeeStatus
from smarthris.schemas.employee import SortOrder


class CourseAnalyticsSortField(str, Enum):
    ENROLMENTS = "enrolments"
    COMPLETED = "completed"
    COMPLETION_RATE = "completion_rate"
    COURSE_CODE = "course_code"


class EmployeeAnalyticsSortField(str, Enum):
    ENROLMENTS = "enrolments"
    COMPLETED = "completed"
    COMPLETION_RATE = "completion_rate"
    EMPLOYEE_NUMBER = "employee_number"


class TrendGranularity(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class LmsSummaryResponse(BaseModel):
    """Overall LMS headcounts and enrolment status totals."""

    total_employees: int
    total_courses: int
    total_enrolments: int
    enrolled: int
    in_progress: int
    completed: int
    cancelled: int


class CourseAnalyticsItem(BaseModel):
    """One course row with enrolment status breakdown."""

    course_code: str
    course_name: str
    category: str
    total_enrolments: int
    enrolled: int
    in_progress: int
    completed: int
    cancelled: int
    completion_percentage: Decimal = Field(max_digits=6, decimal_places=2)


class CourseAnalyticsListResponse(BaseModel):
    items: list[CourseAnalyticsItem]
    total: int
    page: int
    page_size: int
    pages: int


class TopCourseItem(BaseModel):
    course_code: str
    course_name: str
    enrolment_count: int


class TopCoursesResponse(BaseModel):
    items: list[TopCourseItem]
    limit: int


class EmployeeAnalyticsItem(BaseModel):
    employee_number: str
    first_name: str
    last_name: str
    department_id: int
    total_enrolments: int
    enrolled: int
    in_progress: int
    completed: int
    cancelled: int
    completion_percentage: Decimal = Field(max_digits=6, decimal_places=2)


class EmployeeAnalyticsListResponse(BaseModel):
    items: list[EmployeeAnalyticsItem]
    total: int
    page: int
    page_size: int
    pages: int


class EnrolmentTrendItem(BaseModel):
    period_start: date
    period_label: str
    enrolment_count: int


class EnrolmentTrendsResponse(BaseModel):
    granularity: TrendGranularity
    from_date: date
    to_date: date
    items: list[EnrolmentTrendItem]


# Re-export for router type hints / OpenAPI
__all__ = [
    "CourseAnalyticsItem",
    "CourseAnalyticsListResponse",
    "CourseAnalyticsSortField",
    "CourseStatus",
    "CourseEnrolmentStatus",
    "EmployeeAnalyticsItem",
    "EmployeeAnalyticsListResponse",
    "EmployeeAnalyticsSortField",
    "EmployeeStatus",
    "EnrolmentTrendItem",
    "EnrolmentTrendsResponse",
    "LmsSummaryResponse",
    "SortOrder",
    "TopCourseItem",
    "TopCoursesResponse",
    "TrendGranularity",
]
