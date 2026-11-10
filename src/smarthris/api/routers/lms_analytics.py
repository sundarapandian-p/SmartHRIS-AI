"""Read-only LMS analytics routes."""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.models.course import CourseStatus
from smarthris.models.course_enrolment import CourseEnrolmentStatus
from smarthris.models.employee import EmployeeStatus
from smarthris.schemas.lms_analytics import (
    CourseAnalyticsListResponse,
    CourseAnalyticsSortField,
    EmployeeAnalyticsListResponse,
    EmployeeAnalyticsSortField,
    EnrolmentTrendsResponse,
    LmsSummaryResponse,
    SortOrder,
    TopCoursesResponse,
    TrendGranularity,
)
from smarthris.services.lms_analytics_service import LmsAnalyticsService

router = APIRouter(prefix="/analytics/lms", tags=["lms-analytics"])


def get_lms_analytics_service(db: Session = Depends(get_db)) -> LmsAnalyticsService:
    return LmsAnalyticsService(db)


@router.get("/summary", response_model=LmsSummaryResponse)
def get_lms_summary(
    service: LmsAnalyticsService = Depends(get_lms_analytics_service),
) -> LmsSummaryResponse:
    """Overall LMS headcounts and enrolment status totals."""
    return service.get_summary()


@router.get("/courses/top", response_model=TopCoursesResponse)
def get_top_courses(
    limit: int = Query(10, ge=1, le=50),
    status_filter: CourseEnrolmentStatus | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    service: LmsAnalyticsService = Depends(get_lms_analytics_service),
) -> TopCoursesResponse:
    """Top courses by enrolment count (max 50)."""
    return service.list_top_courses(
        limit=limit,
        status_filter=status_filter,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/courses", response_model=CourseAnalyticsListResponse)
def list_course_analytics(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    category: str | None = Query(None),
    course_status: CourseStatus | None = None,
    sort_by: CourseAnalyticsSortField = Query(CourseAnalyticsSortField.ENROLMENTS),
    sort_order: SortOrder = Query(SortOrder.DESC),
    service: LmsAnalyticsService = Depends(get_lms_analytics_service),
) -> CourseAnalyticsListResponse:
    """Paginated course-wise enrolment analytics (includes zero-enrolment courses)."""
    return service.list_courses(
        page=page,
        page_size=page_size,
        category=category,
        course_status=course_status,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/employees", response_model=EmployeeAnalyticsListResponse)
def list_employee_analytics(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    department_id: int | None = Query(None, ge=1),
    employee_status: EmployeeStatus | None = None,
    sort_by: EmployeeAnalyticsSortField = Query(EmployeeAnalyticsSortField.ENROLMENTS),
    sort_order: SortOrder = Query(SortOrder.DESC),
    service: LmsAnalyticsService = Depends(get_lms_analytics_service),
) -> EmployeeAnalyticsListResponse:
    """Paginated employee-wise enrolment analytics (includes employees with none)."""
    return service.list_employees(
        page=page,
        page_size=page_size,
        department_id=department_id,
        employee_status=employee_status,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@router.get("/trends/enrolments", response_model=EnrolmentTrendsResponse)
def list_enrolment_trends(
    from_date: date = Query(...),
    to_date: date = Query(...),
    granularity: TrendGranularity = Query(TrendGranularity.DAY),
    status: CourseEnrolmentStatus | None = None,
    course_code: str | None = Query(None),
    service: LmsAnalyticsService = Depends(get_lms_analytics_service),
) -> EnrolmentTrendsResponse:
    """Date-bucketed enrolment counts (day / week / month)."""
    return service.list_enrolment_trends(
        from_date=from_date,
        to_date=to_date,
        granularity=granularity,
        status=status,
        course_code=course_code,
    )
