"""Consistency and failure-handling tests for HR document indexing."""

from __future__ import annotations

import inspect
import io
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.models.hr_document import HrDocument, HrDocumentStatus, HrDocumentType
from smarthris.models.hr_document_chunk import HrDocumentChunk
from smarthris.rag import faiss_store
from smarthris.rag.embeddings import DocumentProcessingError, reset_embedding_cache_for_tests
from smarthris.rag.faiss_store import (
    ChunkVectorRecord,
    load_manifest,
    manifest_path,
    publish_index_after_mysql_commit,
    rebuild_persisted_index_from_records,
)
from smarthris.rag.storage import save_document_upload
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.services.hr_document_processing_service import (
    HrDocumentProcessingService,
    reconcile_vector_index_from_database,
)

from tests.test_hr_document_processing import MockEncoder, _TrackingSessionFactory, _settings


@pytest.fixture(autouse=True)
def _reset_embeddings() -> None:
    reset_embedding_cache_for_tests()


def _txt_upload(tmp_path: Path, text: str) -> tuple[str, str]:
    result = save_document_upload(
        source=io.BytesIO(text.encode("utf-8")),
        rag_document_dir=tmp_path / "documents",
        original_filename="policy.txt",
    )
    return result.document_id, str(result.storage_path)


def _seed_live_index(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    def embed(texts):
        vectors = np.zeros((len(texts), 8), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    rebuild_persisted_index_from_records(
        [ChunkVectorRecord(chunk_id=999, chunk_text="seeded")],
        settings=settings,
        dimension=8,
        embed_fn=embed,
    )
    manifest = load_manifest(manifest_path(settings))
    assert manifest is not None
    assert manifest.vector_count == 1


def test_mysql_commit_failure_preserves_previous_faiss_index(
    db_session: Session,
    tmp_path: Path,
) -> None:
    _seed_live_index(tmp_path)
    settings = _settings(tmp_path)
    before = load_manifest(manifest_path(settings))
    assert before is not None

    text = ("Commit failure policy text. " * 15).strip()
    doc_id, storage_path = _txt_upload(tmp_path, text)
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="h" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        settings,
        embedding_provider=MockEncoder(),
    )

    with patch.object(
        HrDocumentRepository,
        "mark_indexed",
        side_effect=RuntimeError("simulated MySQL commit failure"),
    ):
        service.process(doc_id)

    after = load_manifest(manifest_path(settings))
    assert after is not None
    assert after.vector_count == before.vector_count

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.FAILED
    assert document.indexed_at is None


def test_faiss_build_failure_preserves_previous_index(
    db_session: Session,
    tmp_path: Path,
) -> None:
    _seed_live_index(tmp_path)
    settings = _settings(tmp_path)
    before = load_manifest(manifest_path(settings))

    text = ("Build failure body. " * 10).strip()
    doc_id, storage_path = _txt_upload(tmp_path, text)
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="i2" * 32,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        settings,
        embedding_provider=MockEncoder(),
    )
    with patch(
        "smarthris.rag.faiss_store.build_index_files_in_directory",
        side_effect=DocumentProcessingError("build failed"),
    ):
        service.process(doc_id)

    after = load_manifest(manifest_path(settings))
    assert after is not None
    assert after.vector_count == before.vector_count


def test_faiss_promotion_failure_preserves_previous_index(
    db_session: Session,
    tmp_path: Path,
) -> None:
    _seed_live_index(tmp_path)
    settings = _settings(tmp_path)
    before = load_manifest(manifest_path(settings))

    text = ("Promotion failure body. " * 12).strip()
    doc_id, storage_path = _txt_upload(tmp_path, text)
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="i" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        settings,
        embedding_provider=MockEncoder(),
    )

    with patch(
        "smarthris.rag.faiss_store.promote_index_directory",
        side_effect=DocumentProcessingError("promotion failed"),
    ):
        service.process(doc_id)

    after = load_manifest(manifest_path(settings))
    assert after is not None
    assert after.vector_count == before.vector_count

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.FAILED
    assert document.indexed_at is None


def test_reprocessing_failure_clears_indexed_at(db_session: Session, tmp_path: Path) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "indexed legacy content")
    indexed_at = datetime(2025, 1, 1, tzinfo=UTC)
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="j" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.INDEXED,
            indexed_at=indexed_at,
        )
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
        embedding_provider=MockEncoder(fail=True),
    )
    service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.FAILED
    assert document.indexed_at is None
    chunks = HrDocumentChunkRepository(db_session).list_by_document_id(doc_id)
    assert chunks[0].chunk_text == "legacy chunk"


def test_concurrent_index_updates_serialize(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    active = {"n": 0}
    peak = {"n": 0}
    lock = threading.Lock()

    def embed(texts):
        with lock:
            active["n"] += 1
            peak["n"] = max(peak["n"], active["n"])
        try:
            vectors = np.zeros((len(texts), 8), dtype=np.float32)
            vectors[:, 0] = 1.0
            return vectors
        finally:
            with lock:
                active["n"] -= 1

    def worker(chunk_id: int) -> None:
        publish_index_after_mysql_commit(
            [ChunkVectorRecord(chunk_id=chunk_id, chunk_text=f"text-{chunk_id}")],
            settings=settings,
            dimension=8,
            embed_fn=embed,
            batch_size=8,
            mysql_commit=lambda: None,
        )

    threads = [threading.Thread(target=worker, args=(cid,)) for cid in range(1001, 1005)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak["n"] == 1


def test_reconcile_rebuilds_index_from_mysql(db_session: Session, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    doc_id, storage_path = _txt_upload(tmp_path, "reconcile body " * 8)
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="k" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.INDEXED,
            indexed_at=datetime.now(UTC),
        )
    )
    chunk = HrDocumentChunk(
        document_id=doc_id,
        chunk_index=0,
        chunk_text="authoritative chunk",
        token_count=2,
    )
    HrDocumentChunkRepository(db_session).add_many([chunk])
    db_session.flush()

    reconcile_vector_index_from_database(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
    )

    manifest = load_manifest(manifest_path(settings))
    assert manifest is not None
    assert manifest.vector_count >= 1


def test_background_unhandled_exception_marks_failed(db_session: Session, tmp_path: Path) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "explode")
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="l" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(),
    )
    with patch(
        "smarthris.services.hr_document_processing_service.extract_document_text",
        side_effect=RuntimeError("unexpected boom"),
    ):
        service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.FAILED


def test_index_update_lock_documents_process_local_single_worker() -> None:
    doc = faiss_store._index_update_lock.__doc__ or ""
    assert "process-local" in doc
    assert "worker" in doc.lower()
    module_source = inspect.getsource(faiss_store)
    assert "one Uvicorn worker" in module_source


def test_mark_failed_fresh_logs_original_when_status_update_fails(
    db_session: Session,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "status update failure")
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="m" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PROCESSING,
        )
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(),
    )
    original = DocumentProcessingError("original promotion failure")

    with caplog.at_level(
        logging.ERROR,
        logger="smarthris.services.hr_document_processing_service",
    ):
        with patch.object(
            HrDocumentRepository,
            "mark_failed",
            side_effect=RuntimeError("simulated status update failure"),
        ):
            service._mark_failed_fresh(
                doc_id,
                "Indexing failed after promotion error",
                original_exc=original,
            )

    combined = caplog.text
    assert "failed during processing before status could be updated" in combined
    assert "Unable to mark HR document" in combined
    assert "original promotion failure" in combined
    assert "simulated status update failure" in combined


def test_process_still_raises_original_when_mark_failed_fresh_fails(
    db_session: Session,
    tmp_path: Path,
) -> None:
    doc_id, storage_path = _txt_upload(tmp_path, "raise original")
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="n" * 64,
            storage_path=storage_path,
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    db_session.flush()

    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        _settings(tmp_path),
        embedding_provider=MockEncoder(),
    )
    with patch.object(
        HrDocumentRepository,
        "mark_processing",
        side_effect=RuntimeError("primary processing failure"),
    ):
        with patch.object(
            HrDocumentRepository,
            "mark_failed",
            side_effect=RuntimeError("simulated status update failure"),
        ):
            with pytest.raises(RuntimeError, match="primary processing failure"):
                service.process(doc_id)
