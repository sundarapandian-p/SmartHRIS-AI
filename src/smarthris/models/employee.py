"""Employee ORM model."""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.course_enrolment import CourseEnrolment
    from smarthris.models.department import Department


class EmployeeStatus(str, enum.Enum):
    """Employment status stored in MySQL as string enums."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class Employee(Base):
    """Employee master record."""

    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    employee_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    work_email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), nullable=False, index=True)
    job_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(150), nullable=True)
    manager_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[EmployeeStatus] = mapped_column(
        Enum(EmployeeStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=EmployeeStatus.ACTIVE,
        server_default=EmployeeStatus.ACTIVE.value,
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

    department: Mapped[Department] = relationship(back_populates="employees")
    manager: Mapped[Employee | None] = relationship(
        remote_side="Employee.id",
        back_populates="direct_reports",
    )
    direct_reports: Mapped[list[Employee]] = relationship(back_populates="manager")
    course_enrolments: Mapped[list[CourseEnrolment]] = relationship(
        back_populates="employee",
    )
