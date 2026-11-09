"""LMS course enrolment ORM model."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.course import Course
    from smarthris.models.employee import Employee


class CourseEnrolmentStatus(str, enum.Enum):
    """Lifecycle status for an employee course enrolment."""

    ENROLLED = "enrolled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CourseEnrolment(Base):
    """One employee enrolment in one LMS course."""

    __tablename__ = "course_enrolments"
    __table_args__ = (
        UniqueConstraint(
            "employee_id",
            "course_id",
            name="uq_course_enrolments_employee_id_course_id",
        ),
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_course_enrolments_score_range",
        ),
        CheckConstraint(
            "completion_date IS NULL OR completion_date >= enrolment_date",
            name="ck_course_enrolments_completion_after_enrolment",
        ),
        CheckConstraint(
            "status <> 'completed' OR completion_date IS NOT NULL",
            name="ck_course_enrolments_completed_requires_date",
        ),
        Index("ix_course_enrolments_course_id_status", "course_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    enrolment_date: Mapped[date] = mapped_column(Date, nullable=False)
    completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[CourseEnrolmentStatus] = mapped_column(
        Enum(CourseEnrolmentStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CourseEnrolmentStatus.ENROLLED,
        server_default=CourseEnrolmentStatus.ENROLLED.value,
        index=True,
    )
    score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    employee: Mapped[Employee] = relationship(back_populates="course_enrolments")
    course: Mapped[Course] = relationship(back_populates="enrolments")
