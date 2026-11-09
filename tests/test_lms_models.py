"""Tests for LMS Course and CourseEnrolment ORM models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError, OperationalError

from smarthris.db.base import Base
from smarthris.models import (
    Course,
    CourseEnrolment,
    CourseEnrolmentStatus,
    CourseStatus,
    Employee,
)
from smarthris.models.employee import EmployeeStatus


def _make_course(**overrides) -> Course:
    values = {
        "course_code": "SAFE-101",
        "course_name": "Workplace Safety",
        "category": "Compliance",
        "duration_hours": Decimal("4.50"),
        "status": CourseStatus.ACTIVE,
    }
    values.update(overrides)
    return Course(**values)


def _make_employee(department_id: int, **overrides) -> Employee:
    values = {
        "employee_number": "E-LMS-1",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada.lms@example.com",
        "department_id": department_id,
        "status": EmployeeStatus.ACTIVE,
    }
    values.update(overrides)
    return Employee(**values)


def test_models_registered_on_base_metadata() -> None:
    assert "courses" in Base.metadata.tables
    assert "course_enrolments" in Base.metadata.tables


def test_course_defaults_and_optional_description(db_session, sample_department) -> None:
    course = _make_course(description=None)
    db_session.add(course)
    db_session.flush()
    assert course.id is not None
    assert course.status is CourseStatus.ACTIVE
    assert course.description is None
    assert course.duration_hours == Decimal("4.50")


def test_course_code_unique(db_session, sample_department) -> None:
    db_session.add(_make_course(course_code="SAFE-101"))
    db_session.flush()
    db_session.add(_make_course(course_code="SAFE-101", course_name="Other"))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_enrolment_relationship_and_unique_pair(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id)
    course = _make_course()
    db_session.add_all([employee, course])
    db_session.flush()

    enrolment = CourseEnrolment(
        employee_id=employee.id,
        course_id=course.id,
        enrolment_date=date(2026, 1, 10),
        status=CourseEnrolmentStatus.ENROLLED,
    )
    db_session.add(enrolment)
    db_session.flush()

    db_session.refresh(employee)
    db_session.refresh(course)
    assert len(employee.course_enrolments) == 1
    assert len(course.enrolments) == 1
    assert enrolment.employee.id == employee.id
    assert enrolment.course.id == course.id

    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 2, 1),
            status=CourseEnrolmentStatus.ENROLLED,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_score_check_constraint(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id, employee_number="E-LMS-2", work_email="b@example.com")
    course = _make_course(course_code="SAFE-102")
    db_session.add_all([employee, course])
    db_session.flush()

    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 1, 10),
            score=Decimal("150.00"),
        )
    )
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.flush()
    db_session.rollback()


def test_completion_date_before_enrolment_rejected(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id, employee_number="E-LMS-3", work_email="c@example.com")
    course = _make_course(course_code="SAFE-103")
    db_session.add_all([employee, course])
    db_session.flush()

    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 5, 1),
            completion_date=date(2026, 4, 1),
            status=CourseEnrolmentStatus.COMPLETED,
        )
    )
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.flush()
    db_session.rollback()


def test_completed_requires_completion_date(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id, employee_number="E-LMS-4", work_email="d@example.com")
    course = _make_course(course_code="SAFE-104")
    db_session.add_all([employee, course])
    db_session.flush()

    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 5, 1),
            completion_date=None,
            status=CourseEnrolmentStatus.COMPLETED,
        )
    )
    with pytest.raises((IntegrityError, OperationalError)):
        db_session.flush()
    db_session.rollback()


def test_employee_fk_restrict_blocks_delete(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id, employee_number="E-LMS-5", work_email="e@example.com")
    course = _make_course(course_code="SAFE-105")
    db_session.add_all([employee, course])
    db_session.flush()
    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 3, 1),
            status=CourseEnrolmentStatus.IN_PROGRESS,
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Employee).where(Employee.id == employee.id))
        db_session.flush()
    db_session.rollback()


def test_course_fk_restrict_blocks_delete(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id, employee_number="E-LMS-6", work_email="f@example.com")
    course = _make_course(course_code="SAFE-106")
    db_session.add_all([employee, course])
    db_session.flush()
    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 3, 1),
            status=CourseEnrolmentStatus.ENROLLED,
        )
    )
    db_session.flush()

    with pytest.raises(IntegrityError):
        db_session.execute(delete(Course).where(Course.id == course.id))
        db_session.flush()
    db_session.rollback()


def test_valid_completed_enrolment_with_score(db_session, sample_department) -> None:
    employee = _make_employee(sample_department.id, employee_number="E-LMS-7", work_email="g@example.com")
    course = _make_course(course_code="SAFE-107")
    db_session.add_all([employee, course])
    db_session.flush()
    enrolment = CourseEnrolment(
        employee_id=employee.id,
        course_id=course.id,
        enrolment_date=date(2026, 1, 1),
        completion_date=date(2026, 1, 20),
        status=CourseEnrolmentStatus.COMPLETED,
        score=Decimal("95.50"),
    )
    db_session.add(enrolment)
    db_session.flush()
    assert enrolment.id is not None
    assert enrolment.score == Decimal("95.50")
