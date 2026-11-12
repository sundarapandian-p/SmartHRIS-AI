"""Integration tests for HR document processing and FAISS indexing."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import ConflictError
from smarthris.models.hr_document import HrDocument, HrDocumentStatus, HrDocumentType
from smarthris.models.hr_document_chunk import HrDocumentChunk
from smarthris.rag.embeddings import DocumentProcessingError, reset_embedding_cache_for_tests
from smarthris.rag.extraction import DocumentExtractionError
from smarthris.rag.faiss_store import load_manifest, manifest_path, model_index_dir
from smarthris.rag.storage import save_document_upload
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.services.hr_document_processing_service import (
    HrDocumentProcessingService,
    request_document_reindex,
)


class MockEncoder:
    def __init__(self, *, dimension: int = 8, fail: bool = False) -> None:
        self.dimension = dimension
        self.fail = fail

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        if self.fail:
            raise RuntimeError("embedding failed")
        vectors = np.zeros((len(texts), self.dimension), dtype=np.float32)
        for i, _text in enumerate(texts):
            vectors[i, i % self.dimension] = 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return vectors / norms


class _TrackingSessionFactory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def __call__(self) -> Session:
        session = self._session

        class _Guard:
            def __getattr__(self, name: str):
                return getattr(session, name)

            def close(self) -> None:
                return None

        return _Guard()  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _reset_embedding_cache() -> None:
    reset_embedding_cache_for_tests()


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="mysql+pymysql://unused@127.0.0.1:3306/smarthris_test",
        rag_document_dir=str(tmp_path / "documents"),
        rag_index_dir=str(tmp_path / "indexes"),
        rag_chunk_size=80,
        rag_chunk_overlap=20,
        embedding_model_name="unit-test-model",
    )


def _txt_upload(tmp_path: Path, text: str) -> tuple[str, str]:
    result = save_document_upload(
        source=io.BytesIO(text.encode("utf-8")),
        rag_document_dir=tmp_path / "documents",
        original_filename="policy.txt",
    )
    return result.document_id, str(result.storage_path)


def _persist_document(
    db_session: Session,
    *,
    doc_id: str,
    storage_path: str,
    checksum: str,
    status: HrDocumentStatus = HrDocumentStatus.PENDING,
) -> None:
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum=checksum,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=status,
        )
    )
    db_session.flush()


def test_successful_processing_persists_chunks_and_index(
    db_session: Session,
    tmp_path: Path,
) -> None:
    text = ("Human resources policy document. " * 20).strip()
    doc_id, storage_path = _txt_upload(tmp_path, text)
    _persist_document(db_session, doc_id=doc_id, storage_path=storage_path, checksum="c" * 64)

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(),
    )
    service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.INDEXED
    chunks = HrDocumentChunkRepository(db_session).list_by_document_id(doc_id)
    assert len(chunks) >= 2
    assert load_manifest(manifest_path(_settings(tmp_path))) is not None
    assert (model_index_dir(_settings(tmp_path)) / "index.faiss").is_file()


def test_failed_extraction_preserves_existing_chunks(db_session: Session, tmp_path: Path) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "valid chunk source " * 5)
    _persist_document(
        db_session,
        doc_id=doc_id,
        storage_path=storage_path,
        checksum="d" * 64,
        status=HrDocumentStatus.INDEXED,
    )
    HrDocumentChunkRepository(db_session).add_many(
        [
            HrDocumentChunk(
                document_id=doc_id,
                chunk_index=0,
                chunk_text="legacy chunk",
                token_count=2,
            )
        ]
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(),
    )
    with patch(
        "smarthris.services.hr_document_processing_service.extract_document_text",
        side_effect=DocumentExtractionError("No extractable text found in document"),
    ):
        service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.FAILED
    chunks = HrDocumentChunkRepository(db_session).list_by_document_id(doc_id)
    assert len(chunks) == 1
    assert chunks[0].chunk_text == "legacy chunk"


def test_failed_embedding_rolls_back_chunk_replacement(db_session: Session, tmp_path: Path) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "replacement text " * 10)
    _persist_document(
        db_session,
        doc_id=doc_id,
        storage_path=storage_path,
        checksum="e" * 64,
        status=HrDocumentStatus.FAILED,
    )
    HrDocumentChunkRepository(db_session).add_many(
        [
            HrDocumentChunk(
                document_id=doc_id,
                chunk_index=0,
                chunk_text="keep me",
                token_count=2,
            )
        ]
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(fail=True),
    )
    service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.FAILED
    chunks = HrDocumentChunkRepository(db_session).list_by_document_id(doc_id)
    assert chunks[0].chunk_text == "keep me"


def test_duplicate_processing_is_skipped(db_session: Session, tmp_path: Path) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "body")
    _persist_document(
        db_session,
        doc_id=doc_id,
        storage_path=storage_path,
        checksum="f" * 64,
        status=HrDocumentStatus.PROCESSING,
    )

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(),
    )
    service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.PROCESSING


def test_request_reindex_rejects_processing_document(db_session: Session, tmp_path: Path) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "x")
    _persist_document(
        db_session,
        doc_id=doc_id,
        storage_path=storage_path,
        checksum="g" * 64,
        status=HrDocumentStatus.PROCESSING,
    )
    with pytest.raises(ConflictError):
        request_document_reindex(db_session, doc_id)
