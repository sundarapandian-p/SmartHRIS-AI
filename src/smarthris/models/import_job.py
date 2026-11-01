"""Bulk employee import job ORM model."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.import_error import ImportError


class ImportFileType(str, enum.Enum):
    CSV = "csv"
    XLSX = "xlsx"


class ImportJobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ImportJob(Base):
    """Tracks one uploaded employee import file and aggregate results."""

    __tablename__ = "import_jobs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    status: Mapped[ImportJobStatus] = mapped_column(
        Enum(ImportJobStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=ImportJobStatus.PENDING,
        server_default=ImportJobStatus.PENDING.value,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[ImportFileType] = mapped_column(
        Enum(ImportFileType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")

    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    errors: Mapped[list[ImportError]] = relationship(
        back_populates="import_job",
        cascade="all, delete-orphan",
    )
