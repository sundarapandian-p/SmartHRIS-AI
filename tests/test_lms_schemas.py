"""Pydantic schema tests for LMS courses and enrolments."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from smarthris.models.course import Course, CourseStatus
from smarthris.models.course_enrolment import CourseEnrolment, CourseEnrolmentStatus
from smarthris.schemas.course import (
    CourseCreate,
    CourseEnrolmentCreate,
    CourseEnrolmentRead,
    CourseRead,
)


def test_course_create_and_read() -> None:
    payload = CourseCreate(
        course_code="HR-200",
        course_name="Harassment Prevention",
        category="Compliance",
        description="Annual training",
        duration_hours=Decimal("2.00"),
    )
    assert payload.status is CourseStatus.ACTIVE

    course = Course(
        id=1,
        course_code=payload.course_code,
        course_name=payload.course_name,
        category=payload.category,
        description=payload.description,
        duration_hours=payload.duration_hours,
        status=CourseStatus.ACTIVE,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    read = CourseRead.model_validate(course)
    assert read.course_code == "HR-200"
    assert read.duration_hours == Decimal("2.00")


def test_course_create_rejects_non_positive_duration() -> None:
    with pytest.raises(ValidationError):
        CourseCreate(
            course_code="HR-201",
            course_name="Bad Duration",
            category="Compliance",
            duration_hours=Decimal("0"),
        )


def test_enrolment_create_score_bounds() -> None:
    CourseEnrolmentCreate(
        employee_id=1,
        course_id=1,
        enrolment_date=date(2026, 1, 1),
        score=Decimal("100"),
    )
    with pytest.raises(ValidationError):
        CourseEnrolmentCreate(
            employee_id=1,
            course_id=1,
            enrolment_date=date(2026, 1, 1),
            score=Decimal("100.01"),
        )
    with pytest.raises(ValidationError):
        CourseEnrolmentCreate(
            employee_id=1,
            course_id=1,
            enrolment_date=date(2026, 1, 1),
            score=Decimal("-1"),
        )


def test_enrolment_create_completion_rules() -> None:
    with pytest.raises(ValidationError, match="completion_date"):
        CourseEnrolmentCreate(
            employee_id=1,
            course_id=1,
            enrolment_date=date(2026, 5, 1),
            completion_date=date(2026, 4, 1),
        )
    with pytest.raises(ValidationError, match="completion_date"):
        CourseEnrolmentCreate(
            employee_id=1,
            course_id=1,
            enrolment_date=date(2026, 5, 1),
            status=CourseEnrolmentStatus.COMPLETED,
            completion_date=None,
        )


def test_enrolment_read_from_orm() -> None:
    enrolment = CourseEnrolment(
        id=9,
        employee_id=3,
        course_id=4,
        enrolment_date=date(2026, 2, 1),
        completion_date=date(2026, 2, 15),
        status=CourseEnrolmentStatus.COMPLETED,
        score=Decimal("88.00"),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    read = CourseEnrolmentRead.model_validate(enrolment)
    dumped = read.model_dump()
    assert dumped["status"] == CourseEnrolmentStatus.COMPLETED
    assert dumped["score"] == Decimal("88.00")
    assert set(dumped) == {
        "id",
        "employee_id",
        "course_id",
        "enrolment_date",
        "completion_date",
        "status",
        "score",
        "created_at",
        "updated_at",
    }
