"""Phase 5.3.3 — LMS analytics repository, service, and API tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.core.exceptions import ValidationError
from smarthris.main import create_app
from smarthris.models.course import Course, CourseStatus
from smarthris.models.course_enrolment import CourseEnrolment, CourseEnrolmentStatus
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.repositories.lms_analytics_repository import LmsAnalyticsRepository
from smarthris.schemas.lms_analytics import (
    CourseAnalyticsSortField,
    EmployeeAnalyticsSortField,
    SortOrder,
    TrendGranularity,
)
from smarthris.services.lms_analytics_service import LmsAnalyticsService


@pytest.fixture
def analytics_api_client(
    db_session: Session,
    integration_database_url: str,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-LMS-Analytics",
        database_url=integration_database_url,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _add_course(
    db_session: Session,
    *,
    code: str,
    name: str = "Course",
    category: str = "Engineering",
    status: CourseStatus = CourseStatus.ACTIVE,
) -> Course:
    course = Course(
        course_code=code,
        course_name=name,
        category=category,
        duration_hours=Decimal("8.00"),
        status=status,
    )
    db_session.add(course)
    db_session.flush()
    return course


def _add_employee(
    db_session: Session,
    sample_department,
    *,
    number: str,
    first_name: str = "Ada",
    last_name: str = "Lovelace",
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
) -> Employee:
    employee = Employee(
        employee_number=number,
        first_name=first_name,
        last_name=last_name,
        work_email=f"{number.lower()}@example.com",
        department_id=sample_department.id,
        status=status,
    )
    db_session.add(employee)
    db_session.flush()
    return employee


def _enrol(
    db_session: Session,
    *,
    employee: Employee,
    course: Course,
    status: CourseEnrolmentStatus,
    enrolment_date: date,
    completion_date: date | None = None,
) -> CourseEnrolment:
    row = CourseEnrolment(
        employee_id=employee.id,
        course_id=course.id,
        enrolment_date=enrolment_date,
        completion_date=completion_date,
        status=status,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_summary_empty_database(db_session: Session) -> None:
    summary = LmsAnalyticsRepository(db_session).get_summary()
    assert summary.total_employees == 0
    assert summary.total_courses == 0
    assert summary.total_enrolments == 0
    assert summary.enrolled == 0
    assert summary.in_progress == 0
    assert summary.completed == 0
    assert summary.cancelled == 0


def test_summary_and_completion_excludes_cancelled(
    db_session: Session,
    sample_department,
) -> None:
    emp = _add_employee(db_session, sample_department, number="A1")
    emp2 = _add_employee(db_session, sample_department, number="A2")
    course = _add_course(db_session, code="C1", name="One")
    zero_course = _add_course(db_session, code="C0", name="Zero")

    _enrol(
        db_session,
        employee=emp,
        course=course,
        status=CourseEnrolmentStatus.COMPLETED,
        enrolment_date=date(2026, 1, 1),
        completion_date=date(2026, 1, 10),
    )
    _enrol(
        db_session,
        employee=emp2,
        course=course,
        status=CourseEnrolmentStatus.ENROLLED,
        enrolment_date=date(2026, 1, 2),
    )
    _enrol(
        db_session,
        employee=emp,
        course=zero_course,
        status=CourseEnrolmentStatus.CANCELLED,
        enrolment_date=date(2026, 1, 3),
    )
    # Force cancelled on C1 as well via another employee path: use emp2 on zero? already used pairs.
    # Add third employee for cancelled on C1.
    emp3 = _add_employee(db_session, sample_department, number="A3")
    _enrol(
        db_session,
        employee=emp3,
        course=course,
        status=CourseEnrolmentStatus.CANCELLED,
        enrolment_date=date(2026, 1, 4),
    )
    _enrol(
        db_session,
        employee=emp2,
        course=zero_course,
        status=CourseEnrolmentStatus.IN_PROGRESS,
        enrolment_date=date(2026, 1, 5),
    )

    summary = LmsAnalyticsRepository(db_session).get_summary()
    assert summary.total_employees == 3
    assert summary.total_courses == 2
    assert summary.total_enrolments == 5
    assert summary.completed == 1
    assert summary.enrolled == 1
    assert summary.in_progress == 1
    assert summary.cancelled == 2

    courses = LmsAnalyticsRepository(db_session).list_course_analytics(
        offset=0,
        limit=10,
        sort_by=CourseAnalyticsSortField.COURSE_CODE,
        sort_order=SortOrder.ASC,
    )
    by_code = {row.course_code: row for row in courses}
    # C1: completed=1, enrolled=1, cancelled=1 → rate = 1/2 = 50
    assert by_code["C1"].total_enrolments == 3
    assert by_code["C1"].completed == 1
    assert by_code["C1"].enrolled == 1
    assert by_code["C1"].cancelled == 1
    assert by_code["C1"].completion_percentage == Decimal("50.00")
    # C0: cancelled=1, in_progress=1 → rate = 0/1 = 0
    assert by_code["C0"].total_enrolments == 2
    assert by_code["C0"].completion_percentage == Decimal("0.00")


def test_zero_enrolment_course_and_zero_denominator(
    db_session: Session,
    sample_department,
) -> None:
    _add_employee(db_session, sample_department, number="Z1")
    lonely = _add_course(db_session, code="LONELY", name="No One")
    rows = LmsAnalyticsRepository(db_session).list_course_analytics(
        offset=0,
        limit=10,
        sort_by=CourseAnalyticsSortField.COURSE_CODE,
        sort_order=SortOrder.ASC,
    )
    assert len(rows) == 1
    assert rows[0].course_code == lonely.course_code
    assert rows[0].total_enrolments == 0
    assert rows[0].completion_percentage == Decimal("0.00")


def test_employee_analytics_includes_zero_and_rates(
    db_session: Session,
    sample_department,
) -> None:
    with_enrolments = _add_employee(
        db_session, sample_department, number="E1", first_name="With"
    )
    without = _add_employee(
        db_session, sample_department, number="E0", first_name="None"
    )
    course = _add_course(db_session, code="PY", name="Python")
    _enrol(
        db_session,
        employee=with_enrolments,
        course=course,
        status=CourseEnrolmentStatus.COMPLETED,
        enrolment_date=date(2026, 2, 1),
        completion_date=date(2026, 2, 10),
    )
    _enrol(
        db_session,
        employee=with_enrolments,
        course=_add_course(db_session, code="SQL", name="SQL"),
        status=CourseEnrolmentStatus.CANCELLED,
        enrolment_date=date(2026, 2, 2),
    )

    rows = LmsAnalyticsRepository(db_session).list_employee_analytics(
        offset=0,
        limit=10,
        sort_by=EmployeeAnalyticsSortField.EMPLOYEE_NUMBER,
        sort_order=SortOrder.ASC,
    )
    by_number = {row.employee_number: row for row in rows}
    assert without.employee_number in by_number
    assert by_number["E0"].total_enrolments == 0
    assert by_number["E0"].completion_percentage == Decimal("0.00")
    # E1: 1 completed + 1 cancelled → denominator 1 → 100%
    assert by_number["E1"].total_enrolments == 2
    assert by_number["E1"].completed == 1
    assert by_number["E1"].cancelled == 1
    assert by_number["E1"].completion_percentage == Decimal("100.00")


def test_top_courses_limit_and_filters(
    db_session: Session,
    sample_department,
) -> None:
    e1 = _add_employee(db_session, sample_department, number="T1")
    e2 = _add_employee(db_session, sample_department, number="T2")
    popular = _add_course(db_session, code="POP", name="Popular")
    niche = _add_course(db_session, code="NICHE", name="Niche")
    _enrol(
        db_session,
        employee=e1,
        course=popular,
        status=CourseEnrolmentStatus.ENROLLED,
        enrolment_date=date(2026, 3, 1),
    )
    _enrol(
        db_session,
        employee=e2,
        course=popular,
        status=CourseEnrolmentStatus.COMPLETED,
        enrolment_date=date(2026, 3, 2),
        completion_date=date(2026, 3, 9),
    )
    _enrol(
        db_session,
        employee=e1,
        course=niche,
        status=CourseEnrolmentStatus.ENROLLED,
        enrolment_date=date(2026, 3, 3),
    )

    top = LmsAnalyticsRepository(db_session).list_top_courses(limit=1)
    assert len(top) == 1
    assert top[0].course_code == "POP"
    assert top[0].enrolment_count == 2

    completed_only = LmsAnalyticsRepository(db_session).list_top_courses(
        limit=10,
        status_filter=CourseEnrolmentStatus.COMPLETED,
    )
    assert len(completed_only) == 1
    assert completed_only[0].course_code == "POP"

    dated = LmsAnalyticsRepository(db_session).list_top_courses(
        limit=10,
        from_date=date(2026, 3, 3),
        to_date=date(2026, 3, 3),
    )
    assert len(dated) == 1
    assert dated[0].course_code == "NICHE"


def test_trends_day_week_month(
    db_session: Session,
    sample_department,
) -> None:
    emp = _add_employee(db_session, sample_department, number="TR1")
    course = _add_course(db_session, code="TRC", name="Trend")
    _enrol(
        db_session,
        employee=emp,
        course=course,
        status=CourseEnrolmentStatus.ENROLLED,
        enrolment_date=date(2026, 1, 5),
    )
    emp2 = _add_employee(db_session, sample_department, number="TR2")
    _enrol(
        db_session,
        employee=emp2,
        course=course,
        status=CourseEnrolmentStatus.ENROLLED,
        enrolment_date=date(2026, 1, 5),
    )
    _enrol(
        db_session,
        employee=emp,
        course=_add_course(db_session, code="TRC2", name="Trend2"),
        status=CourseEnrolmentStatus.COMPLETED,
        enrolment_date=date(2026, 2, 10),
        completion_date=date(2026, 2, 20),
    )

    repo = LmsAnalyticsRepository(db_session)
    daily = repo.list_enrolment_trends(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 2, 28),
        granularity=TrendGranularity.DAY,
    )
    by_day = {row.period_label: row.enrolment_count for row in daily}
    assert by_day["2026-01-05"] == 2
    assert by_day["2026-02-10"] == 1

    weekly = repo.list_enrolment_trends(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 2, 28),
        granularity=TrendGranularity.WEEK,
    )
    assert sum(row.enrolment_count for row in weekly) == 3

    monthly = repo.list_enrolment_trends(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 2, 28),
        granularity=TrendGranularity.MONTH,
    )
    by_month = {row.period_label: row.enrolment_count for row in monthly}
    assert by_month["2026-01"] == 2
    assert by_month["2026-02"] == 1

    filtered = repo.list_enrolment_trends(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 2, 28),
        granularity=TrendGranularity.DAY,
        course_code="TRC",
        status=CourseEnrolmentStatus.ENROLLED,
    )
    assert len(filtered) == 1
    assert filtered[0].enrolment_count == 2


def test_service_validation_and_pagination(db_session: Session, sample_department) -> None:
    service = LmsAnalyticsService(db_session)
    for i in range(3):
        _add_course(db_session, code=f"P{i}", name=f"Page {i}")

    page1 = service.list_courses(page=1, page_size=2, sort_by=CourseAnalyticsSortField.COURSE_CODE)
    assert page1.total == 3
    assert page1.pages == 2
    assert len(page1.items) == 2

    with pytest.raises(ValidationError, match="page_size"):
        service.list_courses(page=1, page_size=0)

    with pytest.raises(ValidationError, match="limit"):
        service.list_top_courses(limit=51)

    with pytest.raises(ValidationError, match="from_date"):
        service.list_top_courses(from_date=date(2026, 1, 1))

    with pytest.raises(ValidationError, match="on or before"):
        service.list_enrolment_trends(
            from_date=date(2026, 2, 1),
            to_date=date(2026, 1, 1),
            granularity=TrendGranularity.DAY,
        )

    with pytest.raises(ValidationError, match="at most"):
        service.list_enrolment_trends(
            from_date=date(2020, 1, 1),
            to_date=date(2026, 1, 1),
            granularity=TrendGranularity.DAY,
        )


def test_api_summary_courses_employees_trends(
    analytics_api_client: TestClient,
    db_session: Session,
    sample_department,
) -> None:
    emp = _add_employee(db_session, sample_department, number="API1")
    course = _add_course(db_session, code="APIC", name="API Course", category="Compliance")
    _add_course(db_session, code="EMPTY", name="Empty", category="Compliance")
    _enrol(
        db_session,
        employee=emp,
        course=course,
        status=CourseEnrolmentStatus.COMPLETED,
        enrolment_date=date(2026, 4, 1),
        completion_date=date(2026, 4, 15),
    )
    db_session.flush()

    summary = analytics_api_client.get("/analytics/lms/summary")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["total_employees"] == 1
    assert body["total_courses"] == 2
    assert body["completed"] == 1

    courses = analytics_api_client.get(
        "/analytics/lms/courses",
        params={
            "category": "Compliance",
            "sort_by": "course_code",
            "sort_order": "asc",
            "page_size": 10,
        },
    )
    assert courses.status_code == 200, courses.text
    assert courses.json()["total"] == 2
    codes = [item["course_code"] for item in courses.json()["items"]]
    assert codes == ["APIC", "EMPTY"]
    empty = next(i for i in courses.json()["items"] if i["course_code"] == "EMPTY")
    assert empty["total_enrolments"] == 0
    assert Decimal(str(empty["completion_percentage"])) == Decimal("0.00")

    top = analytics_api_client.get("/analytics/lms/courses/top", params={"limit": 5})
    assert top.status_code == 200
    assert top.json()["items"][0]["course_code"] == "APIC"

    employees = analytics_api_client.get(
        "/analytics/lms/employees",
        params={"department_id": sample_department.id},
    )
    assert employees.status_code == 200
    assert employees.json()["total"] == 1
    assert employees.json()["items"][0]["employee_number"] == "API1"

    trends = analytics_api_client.get(
        "/analytics/lms/trends/enrolments",
        params={
            "from_date": "2026-04-01",
            "to_date": "2026-04-30",
            "granularity": "day",
        },
    )
    assert trends.status_code == 200
    assert trends.json()["items"][0]["enrolment_count"] == 1


def test_api_invalid_query_params(analytics_api_client: TestClient) -> None:
    bad_limit = analytics_api_client.get(
        "/analytics/lms/courses/top", params={"limit": 100}
    )
    assert bad_limit.status_code == 422

    bad_page = analytics_api_client.get(
        "/analytics/lms/courses", params={"page": 0}
    )
    assert bad_page.status_code == 422

    bad_range = analytics_api_client.get(
        "/analytics/lms/trends/enrolments",
        params={
            "from_date": "2026-05-01",
            "to_date": "2026-04-01",
            "granularity": "day",
        },
    )
    assert bad_range.status_code == 400
    assert bad_range.json()["code"] == "validation_error"

    bad_granularity = analytics_api_client.get(
        "/analytics/lms/trends/enrolments",
        params={
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "granularity": "year",
        },
    )
    assert bad_granularity.status_code == 422
