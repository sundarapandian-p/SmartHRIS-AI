"""Pydantic schemas for HR document metadata and chunks."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from smarthris.models.hr_document import HrDocumentStatus, HrDocumentType


class HrDocumentCreate(BaseModel):
    """Inbound document metadata (no storage paths)."""

    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127)
    checksum: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    doc_type: HrDocumentType = HrDocumentType.OTHER


class HrDocumentRead(BaseModel):
    """Public document metadata (no storage paths or chunk bodies)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    original_filename: str
    content_type: str
    checksum: str
    title: str | None
    doc_type: HrDocumentType
    status: HrDocumentStatus
    uploaded_at: datetime
    indexed_at: datetime | None
    error_message: str | None


class HrDocumentListResponse(BaseModel):
    """Paginated HR document metadata (no storage paths or bodies)."""

    items: list[HrDocumentRead]
    total: int
    page: int
    page_size: int
    pages: int


class HrDocumentChunkCreate(BaseModel):
    """Inbound chunk metadata for persistence by future services."""

    document_id: str = Field(min_length=36, max_length=36)
    chunk_index: int = Field(ge=0)
    chunk_text: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = Field(default=None, max_length=255)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    token_count: int | None = Field(default=None, ge=0)


class HrDocumentChunkRead(BaseModel):
    """Public chunk metadata without chunk_text by default."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: str
    chunk_index: int
    page_number: int | None
    section_title: str | None
    start_offset: int | None
    end_offset: int | None
    token_count: int | None
    created_at: datetime
