"""Tests for HR document Pydantic schemas."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from smarthris.models.hr_document import (
    HrDocument,
    HrDocumentStatus,
    HrDocumentType,
    HrDocumentVisibility,
)
from smarthris.models.hr_document_chunk import HrDocumentChunk
from smarthris.schemas.hr_document import (
    HrDocumentChunkCreate,
    HrDocumentChunkRead,
    HrDocumentCreate,
    HrDocumentRead,
)


def test_document_create_schema_validation() -> None:
    payload = HrDocumentCreate(
        original_filename="handbook.pdf",
        content_type="application/pdf",
        checksum="d" * 64,
        title="Employee Handbook",
        doc_type=HrDocumentType.HANDBOOK,
    )
    assert payload.doc_type is HrDocumentType.HANDBOOK
    assert payload.title == "Employee Handbook"


def test_document_create_rejects_empty_filename() -> None:
    with pytest.raises(ValidationError):
        HrDocumentCreate(
            original_filename="",
            content_type="text/plain",
            checksum="e" * 64,
            doc_type=HrDocumentType.OTHER,
        )


def test_document_read_excludes_storage_path() -> None:
    document = HrDocument(
        id="12345678-1234-4234-8234-123456789abc",
        original_filename="policy.txt",
        content_type="text/plain",
        checksum="f" * 64,
        storage_path="/secret/absolute/path/policy.txt",
        title=None,
        doc_type=HrDocumentType.POLICY,
        visibility=HrDocumentVisibility.HR_ONLY,
        status=HrDocumentStatus.PENDING,
        uploaded_at=datetime.now(timezone.utc),
        indexed_at=None,
        error_message=None,
    )
    read = HrDocumentRead.model_validate(document)
    dumped = read.model_dump()
    assert "storage_path" not in dumped
    assert dumped["original_filename"] == "policy.txt"
    assert dumped["status"] == HrDocumentStatus.PENDING


def test_chunk_read_excludes_chunk_text() -> None:
    chunk = HrDocumentChunk(
        id=1,
        document_id="12345678-1234-4234-8234-123456789abc",
        chunk_index=0,
        chunk_text="Confidential policy body that must not appear in list metadata.",
        page_number=2,
        section_title="Leave",
        start_offset=0,
        end_offset=10,
        token_count=4,
        created_at=datetime.now(timezone.utc),
    )
    read = HrDocumentChunkRead.model_validate(chunk)
    dumped = read.model_dump()
    assert "chunk_text" not in dumped
    assert dumped["chunk_index"] == 0
    assert dumped["section_title"] == "Leave"


def test_chunk_create_schema_accepts_optional_metadata() -> None:
    payload = HrDocumentChunkCreate(
        document_id="12345678-1234-4234-8234-123456789abc",
        chunk_index=1,
        chunk_text="Section body",
        page_number=None,
        section_title=None,
        start_offset=None,
        end_offset=None,
        token_count=None,
    )
    assert payload.chunk_index == 1
    assert payload.page_number is None
