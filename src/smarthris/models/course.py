"""LMS course ORM model."""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.course_enrolment import CourseEnrolment


class CourseStatus(str, enum.Enum):
    """Publication status for an LMS course."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class Course(Base):
    """LMS course master record."""

    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    course_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    course_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    status: Mapped[CourseStatus] = mapped_column(
        Enum(CourseStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=CourseStatus.ACTIVE,
        server_default=CourseStatus.ACTIVE.value,
    )
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

    enrolments: Mapped[list[CourseEnrolment]] = relationship(
        back_populates="course",
    )
