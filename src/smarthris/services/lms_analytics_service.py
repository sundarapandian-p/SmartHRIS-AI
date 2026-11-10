"""LMS analytics business rules and response mapping (read-only)."""

from __future__ import annotations

import math
from datetime import date

from sqlalchemy.orm import Session

from smarthris.core.exceptions import ValidationError
from smarthris.models.course import CourseStatus
from smarthris.models.course_enrolment import CourseEnrolmentStatus
from smarthris.models.employee import EmployeeStatus
from smarthris.repositories.lms_analytics_repository import LmsAnalyticsRepository
from smarthris.schemas.lms_analytics import (
    CourseAnalyticsItem,
    CourseAnalyticsListResponse,
    CourseAnalyticsSortField,
    EmployeeAnalyticsItem,
    EmployeeAnalyticsListResponse,
    EmployeeAnalyticsSortField,
    EnrolmentTrendItem,
    EnrolmentTrendsResponse,
    LmsSummaryResponse,
    SortOrder,
    TopCourseItem,
    TopCoursesResponse,
    TrendGranularity,
)

_MAX_PAGE_SIZE = 100
_MAX_TOP_LIMIT = 50
_MAX_TREND_SPAN_DAYS = {
    TrendGranularity.DAY: 366,
    TrendGranularity.WEEK: 366 * 2,
    TrendGranularity.MONTH: 366 * 5,
}


class LmsAnalyticsService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = LmsAnalyticsRepository(session)

    def get_summary(self) -> LmsSummaryResponse:
        row = self._repo.get_summary()
        return LmsSummaryResponse(
            total_employees=row.total_employees,
            total_courses=row.total_courses,
            total_enrolments=row.total_enrolments,
            enrolled=row.enrolled,
            in_progress=row.in_progress,
            completed=row.completed,
            cancelled=row.cancelled,
        )

    def list_courses(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        course_status: CourseStatus | None = None,
        sort_by: CourseAnalyticsSortField = CourseAnalyticsSortField.ENROLMENTS,
        sort_order: SortOrder = SortOrder.DESC,
    ) -> CourseAnalyticsListResponse:
        page, page_size = self._validate_pagination(page, page_size)
        category = category.strip() if category else None
        if category == "":
            category = None

        total = self._repo.count_course_analytics(
            category=category, course_status=course_status
        )
        offset = (page - 1) * page_size
        rows = self._repo.list_course_analytics(
            offset=offset,
            limit=page_size,
            category=category,
            course_status=course_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        pages = math.ceil(total / page_size) if total else 0
        return CourseAnalyticsListResponse(
            items=[
                CourseAnalyticsItem(
                    course_code=row.course_code,
                    course_name=row.course_name,
                    category=row.category,
                    total_enrolments=row.total_enrolments,
                    enrolled=row.enrolled,
                    in_progress=row.in_progress,
                    completed=row.completed,
                    cancelled=row.cancelled,
                    completion_percentage=row.completion_percentage,
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def list_top_courses(
        self,
        *,
        limit: int = 10,
        status_filter: CourseEnrolmentStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> TopCoursesResponse:
        if limit < 1 or limit > _MAX_TOP_LIMIT:
            raise ValidationError(f"limit must be between 1 and {_MAX_TOP_LIMIT}")
        self._validate_optional_date_range(from_date, to_date)

        rows = self._repo.list_top_courses(
            limit=limit,
            status_filter=status_filter,
            from_date=from_date,
            to_date=to_date,
        )
        return TopCoursesResponse(
            items=[
                TopCourseItem(
                    course_code=row.course_code,
                    course_name=row.course_name,
                    enrolment_count=row.enrolment_count,
                )
                for row in rows
            ],
            limit=limit,
        )

    def list_employees(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        department_id: int | None = None,
        employee_status: EmployeeStatus | None = None,
        sort_by: EmployeeAnalyticsSortField = EmployeeAnalyticsSortField.ENROLMENTS,
        sort_order: SortOrder = SortOrder.DESC,
    ) -> EmployeeAnalyticsListResponse:
        page, page_size = self._validate_pagination(page, page_size)
        if department_id is not None and department_id < 1:
            raise ValidationError("department_id must be >= 1")

        total = self._repo.count_employee_analytics(
            department_id=department_id, employee_status=employee_status
        )
        offset = (page - 1) * page_size
        rows = self._repo.list_employee_analytics(
            offset=offset,
            limit=page_size,
            department_id=department_id,
            employee_status=employee_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        pages = math.ceil(total / page_size) if total else 0
        return EmployeeAnalyticsListResponse(
            items=[
                EmployeeAnalyticsItem(
                    employee_number=row.employee_number,
                    first_name=row.first_name,
                    last_name=row.last_name,
                    department_id=row.department_id,
                    total_enrolments=row.total_enrolments,
                    enrolled=row.enrolled,
                    in_progress=row.in_progress,
                    completed=row.completed,
                    cancelled=row.cancelled,
                    completion_percentage=row.completion_percentage,
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def list_enrolment_trends(
        self,
        *,
        from_date: date,
        to_date: date,
        granularity: TrendGranularity = TrendGranularity.DAY,
        status: CourseEnrolmentStatus | None = None,
        course_code: str | None = None,
    ) -> EnrolmentTrendsResponse:
        self._validate_required_date_range(from_date, to_date, granularity)
        course_code = course_code.strip() if course_code else None
        if course_code == "":
            course_code = None

        rows = self._repo.list_enrolment_trends(
            from_date=from_date,
            to_date=to_date,
            granularity=granularity,
            status=status,
            course_code=course_code,
        )
        return EnrolmentTrendsResponse(
            granularity=granularity,
            from_date=from_date,
            to_date=to_date,
            items=[
                EnrolmentTrendItem(
                    period_start=row.period_start,
                    period_label=row.period_label,
                    enrolment_count=row.enrolment_count,
                )
                for row in rows
            ],
        )

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> tuple[int, int]:
        if page < 1:
            raise ValidationError("page must be >= 1")
        if page_size < 1 or page_size > _MAX_PAGE_SIZE:
            raise ValidationError(f"page_size must be between 1 and {_MAX_PAGE_SIZE}")
        return page, page_size

    @staticmethod
    def _validate_optional_date_range(
        from_date: date | None,
        to_date: date | None,
    ) -> None:
        if from_date is None and to_date is None:
            return
        if from_date is None or to_date is None:
            raise ValidationError("from_date and to_date must be provided together")
        if from_date > to_date:
            raise ValidationError("from_date must be on or before to_date")

    @staticmethod
    def _validate_required_date_range(
        from_date: date,
        to_date: date,
        granularity: TrendGranularity,
    ) -> None:
        if from_date > to_date:
            raise ValidationError("from_date must be on or before to_date")
        span_days = (to_date - from_date).days
        max_span = _MAX_TREND_SPAN_DAYS[granularity]
        if span_days > max_span:
            raise ValidationError(
                f"date range for {granularity.value} trends must be at most "
                f"{max_span} days (got {span_days})"
            )
