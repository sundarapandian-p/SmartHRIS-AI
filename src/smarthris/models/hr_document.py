"""HR document metadata and extracted chunk ORM models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.hr_document_chunk import HrDocumentChunk


class HrDocumentType(str, enum.Enum):
    """Classification of an ingested HR document."""

    POLICY = "policy"
    HANDBOOK = "handbook"
    OTHER = "other"


class HrDocumentStatus(str, enum.Enum):
    """Indexing pipeline status for an HR document."""

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class HrDocument(Base):
    """Metadata for one stored HR document (no employee/salary fields)."""

    __tablename__ = "hr_documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    doc_type: Mapped[HrDocumentType] = mapped_column(
        Enum(HrDocumentType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    status: Mapped[HrDocumentStatus] = mapped_column(
        Enum(HrDocumentStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=HrDocumentStatus.PENDING,
        server_default=HrDocumentStatus.PENDING.value,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunks: Mapped[list[HrDocumentChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )
