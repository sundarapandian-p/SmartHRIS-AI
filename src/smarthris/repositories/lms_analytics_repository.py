"""SQL aggregation queries for LMS analytics (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, case, cast, func, literal_column, select
from sqlalchemy.orm import Session
from sqlalchemy.types import Date, Numeric

from smarthris.models.course import Course, CourseStatus
from smarthris.models.course_enrolment import CourseEnrolment, CourseEnrolmentStatus
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.schemas.lms_analytics import (
    CourseAnalyticsSortField,
    EmployeeAnalyticsSortField,
    SortOrder,
    TrendGranularity,
)


def _status_count(status: CourseEnrolmentStatus):
    return func.coalesce(
        func.sum(
            case((CourseEnrolment.status == status, 1), else_=0),
        ),
        0,
    )


def _completion_percentage_expr(completed_col, enrolled_col, in_progress_col):
    """completed / (enrolled + in_progress + completed); 0 when denominator is 0."""
    denominator = enrolled_col + in_progress_col + completed_col
    return case(
        (denominator == 0, cast(0, Numeric(6, 2))),
        else_=cast(
            (completed_col * 100) / denominator,
            Numeric(6, 2),
        ),
    )


@dataclass(frozen=True, slots=True)
class LmsSummaryRow:
    total_employees: int
    total_courses: int
    total_enrolments: int
    enrolled: int
    in_progress: int
    completed: int
    cancelled: int


@dataclass(frozen=True, slots=True)
class CourseAnalyticsRow:
    course_code: str
    course_name: str
    category: str
    total_enrolments: int
    enrolled: int
    in_progress: int
    completed: int
    cancelled: int
    completion_percentage: Decimal


@dataclass(frozen=True, slots=True)
class TopCourseRow:
    course_code: str
    course_name: str
    enrolment_count: int


@dataclass(frozen=True, slots=True)
class EmployeeAnalyticsRow:
    employee_number: str
    first_name: str
    last_name: str
    department_id: int
    total_enrolments: int
    enrolled: int
    in_progress: int
    completed: int
    cancelled: int
    completion_percentage: Decimal


@dataclass(frozen=True, slots=True)
class EnrolmentTrendRow:
    period_start: date
    period_label: str
    enrolment_count: int


class LmsAnalyticsRepository:
    """Read-only LMS analytics aggregates. Never loads full enrolment tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_summary(self) -> LmsSummaryRow:
        total_employees = int(
            self._session.scalar(select(func.count()).select_from(Employee)) or 0
        )
        total_courses = int(
            self._session.scalar(select(func.count()).select_from(Course)) or 0
        )

        enrolled = _status_count(CourseEnrolmentStatus.ENROLLED)
        in_progress = _status_count(CourseEnrolmentStatus.IN_PROGRESS)
        completed = _status_count(CourseEnrolmentStatus.COMPLETED)
        cancelled = _status_count(CourseEnrolmentStatus.CANCELLED)

        row = self._session.execute(
            select(
                func.count().label("total_enrolments"),
                enrolled.label("enrolled"),
                in_progress.label("in_progress"),
                completed.label("completed"),
                cancelled.label("cancelled"),
            ).select_from(CourseEnrolment)
        ).one()

        return LmsSummaryRow(
            total_employees=total_employees,
            total_courses=total_courses,
            total_enrolments=int(row.total_enrolments or 0),
            enrolled=int(row.enrolled or 0),
            in_progress=int(row.in_progress or 0),
            completed=int(row.completed or 0),
            cancelled=int(row.cancelled or 0),
        )

    def count_course_analytics(
        self,
        *,
        category: str | None = None,
        course_status: CourseStatus | None = None,
    ) -> int:
        query = select(func.count()).select_from(Course)
        query = self._apply_course_filters(query, category=category, course_status=course_status)
        return int(self._session.scalar(query) or 0)

    def list_course_analytics(
        self,
        *,
        offset: int,
        limit: int,
        category: str | None = None,
        course_status: CourseStatus | None = None,
        sort_by: CourseAnalyticsSortField = CourseAnalyticsSortField.ENROLMENTS,
        sort_order: SortOrder = SortOrder.DESC,
    ) -> list[CourseAnalyticsRow]:
        enrolled = _status_count(CourseEnrolmentStatus.ENROLLED)
        in_progress = _status_count(CourseEnrolmentStatus.IN_PROGRESS)
        completed = _status_count(CourseEnrolmentStatus.COMPLETED)
        cancelled = _status_count(CourseEnrolmentStatus.CANCELLED)
        total_enrolments = func.coalesce(func.count(CourseEnrolment.id), 0)
        completion_pct = _completion_percentage_expr(completed, enrolled, in_progress)

        query = (
            select(
                Course.course_code,
                Course.course_name,
                Course.category,
                total_enrolments.label("total_enrolments"),
                enrolled.label("enrolled"),
                in_progress.label("in_progress"),
                completed.label("completed"),
                cancelled.label("cancelled"),
                completion_pct.label("completion_percentage"),
            )
            .select_from(Course)
            .outerjoin(CourseEnrolment, CourseEnrolment.course_id == Course.id)
            .group_by(Course.id, Course.course_code, Course.course_name, Course.category)
        )
        query = self._apply_course_filters(
            query, category=category, course_status=course_status
        )
        query = self._order_course_analytics(
            query,
            sort_by=sort_by,
            sort_order=sort_order,
            total_enrolments=total_enrolments,
            completed=completed,
            completion_pct=completion_pct,
        )
        query = query.offset(offset).limit(limit)
        rows = self._session.execute(query).all()
        return [
            CourseAnalyticsRow(
                course_code=str(row.course_code),
                course_name=str(row.course_name),
                category=str(row.category),
                total_enrolments=int(row.total_enrolments or 0),
                enrolled=int(row.enrolled or 0),
                in_progress=int(row.in_progress or 0),
                completed=int(row.completed or 0),
                cancelled=int(row.cancelled or 0),
                completion_percentage=Decimal(str(row.completion_percentage or 0)),
            )
            for row in rows
        ]

    def list_top_courses(
        self,
        *,
        limit: int,
        status_filter: CourseEnrolmentStatus | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[TopCourseRow]:
        query = (
            select(
                Course.course_code,
                Course.course_name,
                func.count(CourseEnrolment.id).label("enrolment_count"),
            )
            .select_from(CourseEnrolment)
            .join(Course, Course.id == CourseEnrolment.course_id)
        )
        if status_filter is not None:
            query = query.where(CourseEnrolment.status == status_filter)
        if from_date is not None:
            query = query.where(CourseEnrolment.enrolment_date >= from_date)
        if to_date is not None:
            query = query.where(CourseEnrolment.enrolment_date <= to_date)
        query = (
            query.group_by(Course.id, Course.course_code, Course.course_name)
            .order_by(func.count(CourseEnrolment.id).desc(), Course.course_code.asc())
            .limit(limit)
        )
        rows = self._session.execute(query).all()
        return [
            TopCourseRow(
                course_code=str(row.course_code),
                course_name=str(row.course_name),
                enrolment_count=int(row.enrolment_count or 0),
            )
            for row in rows
        ]

    def count_employee_analytics(
        self,
        *,
        department_id: int | None = None,
        employee_status: EmployeeStatus | None = None,
    ) -> int:
        query = select(func.count()).select_from(Employee)
        query = self._apply_employee_filters(
            query, department_id=department_id, employee_status=employee_status
        )
        return int(self._session.scalar(query) or 0)

    def list_employee_analytics(
        self,
        *,
        offset: int,
        limit: int,
        department_id: int | None = None,
        employee_status: EmployeeStatus | None = None,
        sort_by: EmployeeAnalyticsSortField = EmployeeAnalyticsSortField.ENROLMENTS,
        sort_order: SortOrder = SortOrder.DESC,
    ) -> list[EmployeeAnalyticsRow]:
        enrolled = _status_count(CourseEnrolmentStatus.ENROLLED)
        in_progress = _status_count(CourseEnrolmentStatus.IN_PROGRESS)
        completed = _status_count(CourseEnrolmentStatus.COMPLETED)
        cancelled = _status_count(CourseEnrolmentStatus.CANCELLED)
        total_enrolments = func.coalesce(func.count(CourseEnrolment.id), 0)
        completion_pct = _completion_percentage_expr(completed, enrolled, in_progress)

        query = (
            select(
                Employee.employee_number,
                Employee.first_name,
                Employee.last_name,
                Employee.department_id,
                total_enrolments.label("total_enrolments"),
                enrolled.label("enrolled"),
                in_progress.label("in_progress"),
                completed.label("completed"),
                cancelled.label("cancelled"),
                completion_pct.label("completion_percentage"),
            )
            .select_from(Employee)
            .outerjoin(CourseEnrolment, CourseEnrolment.employee_id == Employee.id)
            .group_by(
                Employee.id,
                Employee.employee_number,
                Employee.first_name,
                Employee.last_name,
                Employee.department_id,
            )
        )
        query = self._apply_employee_filters(
            query, department_id=department_id, employee_status=employee_status
        )
        query = self._order_employee_analytics(
            query,
            sort_by=sort_by,
            sort_order=sort_order,
            total_enrolments=total_enrolments,
            completed=completed,
            completion_pct=completion_pct,
        )
        query = query.offset(offset).limit(limit)
        rows = self._session.execute(query).all()
        return [
            EmployeeAnalyticsRow(
                employee_number=str(row.employee_number),
                first_name=str(row.first_name),
                last_name=str(row.last_name),
                department_id=int(row.department_id),
                total_enrolments=int(row.total_enrolments or 0),
                enrolled=int(row.enrolled or 0),
                in_progress=int(row.in_progress or 0),
                completed=int(row.completed or 0),
                cancelled=int(row.cancelled or 0),
                completion_percentage=Decimal(str(row.completion_percentage or 0)),
            )
            for row in rows
        ]

    def list_enrolment_trends(
        self,
        *,
        from_date: date,
        to_date: date,
        granularity: TrendGranularity,
        status: CourseEnrolmentStatus | None = None,
        course_code: str | None = None,
    ) -> list[EnrolmentTrendRow]:
        period_start, period_label = self._period_expressions(granularity)

        query = select(
            period_start.label("period_start"),
            period_label.label("period_label"),
            func.count(CourseEnrolment.id).label("enrolment_count"),
        ).select_from(CourseEnrolment)

        if course_code is not None:
            query = query.join(Course, Course.id == CourseEnrolment.course_id).where(
                Course.course_code == course_code
            )

        query = query.where(CourseEnrolment.enrolment_date >= from_date).where(
            CourseEnrolment.enrolment_date <= to_date
        )
        if status is not None:
            query = query.where(CourseEnrolment.status == status)

        query = query.group_by(period_start, period_label).order_by(period_start.asc())
        rows = self._session.execute(query).all()
        return [
            EnrolmentTrendRow(
                period_start=row.period_start
                if isinstance(row.period_start, date)
                else date.fromisoformat(str(row.period_start)[:10]),
                period_label=str(row.period_label),
                enrolment_count=int(row.enrolment_count or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _period_expressions(granularity: TrendGranularity):
        enrolment_date = CourseEnrolment.enrolment_date
        if granularity is TrendGranularity.DAY:
            return enrolment_date, func.date_format(enrolment_date, "%Y-%m-%d")
        if granularity is TrendGranularity.WEEK:
            # Monday of the week as period_start (MySQL WEEKDAY: Mon=0).
            period_start = cast(
                func.date_sub(
                    enrolment_date,
                    literal_column(
                        "INTERVAL WEEKDAY(course_enrolments.enrolment_date) DAY"
                    ),
                ),
                Date,
            )
            period_label = func.date_format(enrolment_date, "%x-W%v")
            return period_start, period_label
        # MONTH
        period_start = cast(func.date_format(enrolment_date, "%Y-%m-01"), Date)
        period_label = func.date_format(enrolment_date, "%Y-%m")
        return period_start, period_label

    @staticmethod
    def _apply_course_filters(
        query: Select,
        *,
        category: str | None,
        course_status: CourseStatus | None,
    ) -> Select:
        if category is not None:
            query = query.where(Course.category == category)
        if course_status is not None:
            query = query.where(Course.status == course_status)
        return query

    @staticmethod
    def _apply_employee_filters(
        query: Select,
        *,
        department_id: int | None,
        employee_status: EmployeeStatus | None,
    ) -> Select:
        if department_id is not None:
            query = query.where(Employee.department_id == department_id)
        if employee_status is not None:
            query = query.where(Employee.status == employee_status)
        return query

    @staticmethod
    def _order_course_analytics(
        query: Select,
        *,
        sort_by: CourseAnalyticsSortField,
        sort_order: SortOrder,
        total_enrolments,
        completed,
        completion_pct,
    ) -> Select:
        column = {
            CourseAnalyticsSortField.ENROLMENTS: total_enrolments,
            CourseAnalyticsSortField.COMPLETED: completed,
            CourseAnalyticsSortField.COMPLETION_RATE: completion_pct,
            CourseAnalyticsSortField.COURSE_CODE: Course.course_code,
        }[sort_by]
        primary = column.asc() if sort_order == SortOrder.ASC else column.desc()
        return query.order_by(primary, Course.course_code.asc())

    @staticmethod
    def _order_employee_analytics(
        query: Select,
        *,
        sort_by: EmployeeAnalyticsSortField,
        sort_order: SortOrder,
        total_enrolments,
        completed,
        completion_pct,
    ) -> Select:
        column = {
            EmployeeAnalyticsSortField.ENROLMENTS: total_enrolments,
            EmployeeAnalyticsSortField.COMPLETED: completed,
            EmployeeAnalyticsSortField.COMPLETION_RATE: completion_pct,
            EmployeeAnalyticsSortField.EMPLOYEE_NUMBER: Employee.employee_number,
        }[sort_by]
        primary = column.asc() if sort_order == SortOrder.ASC else column.desc()
        return query.order_by(primary, Employee.employee_number.asc())
