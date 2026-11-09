"""Row-level bulk import validation error ORM model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.import_job import ImportJob


class ImportError(Base):
    """One validation or persistence failure for a row in an import job."""

    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("import_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    employee_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    record_key: Mapped[str | None] = mapped_column(
        String(101),
        nullable=True,
        doc=(
            "Business key for non-employee imports "
            "(e.g. course_code, or employee_number|course_code)."
        ),
    )
    field: Mapped[str | None] = mapped_column(String(100), nullable=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    import_job: Mapped[ImportJob] = relationship(back_populates="errors")
