"""Pydantic schemas for LMS courses and enrolments."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from smarthris.models.course import CourseStatus
from smarthris.models.course_enrolment import CourseEnrolmentStatus


class CourseCreate(BaseModel):
    """Inbound course master data."""

    course_code: str = Field(min_length=1, max_length=50)
    course_name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    description: str | None = None
    duration_hours: Decimal = Field(gt=0, max_digits=6, decimal_places=2)
    status: CourseStatus = CourseStatus.ACTIVE


class CourseRead(BaseModel):
    """Public course representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    course_code: str
    course_name: str
    category: str
    description: str | None
    duration_hours: Decimal
    status: CourseStatus
    created_at: datetime
    updated_at: datetime


class CourseEnrolmentCreate(BaseModel):
    """Inbound enrolment data (no internal timestamps)."""

    employee_id: int = Field(gt=0)
    course_id: int = Field(gt=0)
    enrolment_date: date
    completion_date: date | None = None
    status: CourseEnrolmentStatus = CourseEnrolmentStatus.ENROLLED
    score: Decimal | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)

    @field_validator("score")
    @classmethod
    def score_in_range(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return value
        if value < 0 or value > 100:
            raise ValueError("score must be between 0 and 100")
        return value

    @model_validator(mode="after")
    def validate_completion_rules(self) -> CourseEnrolmentCreate:
        if self.completion_date is not None and self.completion_date < self.enrolment_date:
            raise ValueError("completion_date must not precede enrolment_date")
        if self.status is CourseEnrolmentStatus.COMPLETED and self.completion_date is None:
            raise ValueError("completed enrolments require a completion_date")
        return self


class CourseEnrolmentRead(BaseModel):
    """Public enrolment representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: int
    course_id: int
    enrolment_date: date
    completion_date: date | None
    status: CourseEnrolmentStatus
    score: Decimal | None
    created_at: datetime
    updated_at: datetime
