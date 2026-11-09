"""Tests for HR document ORM models and metadata registration."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from smarthris.db.base import Base
from smarthris.models import HrDocument, HrDocumentChunk, HrDocumentStatus, HrDocumentType


def _make_document(**overrides) -> HrDocument:
    values = {
        "original_filename": "leave-policy.pdf",
        "content_type": "application/pdf",
        "checksum": "a" * 64,
        "storage_path": "documents/leave-policy.pdf",
        "doc_type": HrDocumentType.POLICY,
    }
    values.update(overrides)
    return HrDocument(**values)


def test_document_uuid_generated_by_default(db_session) -> None:
    document = _make_document(checksum="0" * 64)
    db_session.add(document)
    db_session.flush()
    assert document.id is not None
    UUID(document.id)


def test_document_default_status_is_pending(db_session) -> None:
    document = _make_document(checksum="1" * 64)
    db_session.add(document)
    db_session.flush()
    assert document.status is HrDocumentStatus.PENDING


def test_document_type_values() -> None:
    assert {member.value for member in HrDocumentType} == {
        "policy",
        "handbook",
        "other",
    }
    assert {member.value for member in HrDocumentStatus} == {
        "pending",
        "processing",
        "indexed",
        "failed",
    }


def test_models_registered_on_base_metadata() -> None:
    assert "hr_documents" in Base.metadata.tables
    assert "hr_document_chunks" in Base.metadata.tables


def test_document_chunk_relationship_and_optional_fields(db_session) -> None:
    document = _make_document(
        title="Leave Policy",
        checksum="b" * 64,
    )
    chunk = HrDocumentChunk(
        chunk_index=0,
        chunk_text="Employees may request leave.",
        page_number=None,
        section_title=None,
        start_offset=None,
        end_offset=None,
        token_count=None,
    )
    document.chunks.append(chunk)
    db_session.add(document)
    db_session.flush()

    db_session.refresh(document)
    assert document.title == "Leave Policy"
    assert document.indexed_at is None
    assert document.error_message is None
    assert len(document.chunks) == 1
    assert document.chunks[0].document_id == document.id
    assert document.chunks[0].page_number is None
    assert document.chunks[0].section_title is None


def test_unique_document_id_chunk_index_constraint(db_session) -> None:
    document = _make_document(checksum="c" * 64)
    db_session.add(document)
    db_session.flush()

    db_session.add(
        HrDocumentChunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="first",
        )
    )
    db_session.flush()

    db_session.add(
        HrDocumentChunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
