"""Concurrency tests for the document indexing pipeline lock."""

from __future__ import annotations

import io
import threading
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session, sessionmaker

from smarthris.db.session import create_session_factory
from smarthris.models.hr_document import HrDocument, HrDocumentStatus, HrDocumentType
from smarthris.rag.bm25_store import reset_bm25_cache_for_tests, search_chunks_by_query
from smarthris.rag.embeddings import reset_embedding_cache_for_tests
from smarthris.rag.faiss_search import reset_search_index_cache_for_tests
from smarthris.rag.storage import save_document_upload
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.services import hr_document_processing_service as processing_module
from smarthris.services.hr_document_processing_service import HrDocumentProcessingService

from tests.test_hr_document_processing import MockEncoder, _TrackingSessionFactory, _settings


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_embedding_cache_for_tests()
    reset_search_index_cache_for_tests()
    reset_bm25_cache_for_tests()


@pytest.fixture(autouse=True)
def _fake_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_hr_document_hybrid_search import FakeBM25Okapi

    monkeypatch.setattr(
        "smarthris.rag.bm25_store.build_bm25_okapi",
        lambda corpus: FakeBM25Okapi(corpus),
    )


def _persist_pending(
    session: Session,
    tmp_path,
    *,
    suffix: str,
    text: str,
) -> str:
    upload = save_document_upload(
        source=io.BytesIO(text.encode("utf-8")),
        rag_document_dir=tmp_path / "documents",
        original_filename=f"{suffix}.txt",
    )
    HrDocumentRepository(session).add(
        HrDocument(
            id=upload.document_id,
            original_filename=f"{suffix}.txt",
            content_type="text/plain",
            checksum=f"concurrency-{suffix}"[:64].ljust(64, "0"),
            storage_path=str(upload.storage_path),
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    session.commit()
    return upload.document_id


def _persist_pending_committed(
    processing_session_factory: sessionmaker[Session],
    tmp_path,
    *,
    suffix: str,
    text: str,
) -> str:
    session = processing_session_factory()
    try:
        return _persist_pending(session, tmp_path, suffix=suffix, text=text)
    finally:
        session.close()


@pytest.fixture
def processing_session_factory(integration_engine) -> sessionmaker[Session]:
    return create_session_factory(integration_engine)


def test_document_indexing_pipeline_lock_blocks_second_worker() -> None:
    """The same lock used by processing must serialize overlapping critical sections."""
    lock = processing_module._document_indexing_pipeline_lock
    first_holding = threading.Event()
    second_blocked = threading.Event()
    release_first = threading.Event()

    def worker_a() -> None:
        with lock:
            first_holding.set()
            assert release_first.wait(timeout=10)

    def worker_b() -> None:
        assert first_holding.wait(timeout=10)
        if lock.locked():
            second_blocked.set()
        with lock:
            pass

    thread_a = threading.Thread(target=worker_a, name="pipeline-a")
    thread_b = threading.Thread(target=worker_b, name="pipeline-b")
    thread_a.start()
    assert first_holding.wait(timeout=10)
    thread_b.start()
    assert second_blocked.wait(timeout=10)
    release_first.set()
    thread_a.join(timeout=10)
    thread_b.join(timeout=10)
    assert not thread_a.is_alive()
    assert not thread_b.is_alive()


def test_concurrent_indexing_locked_body_never_overlaps(
    processing_session_factory: sessionmaker[Session],
    tmp_path,
) -> None:
    """Two processing threads must not run inside _replace_chunks_and_index_locked at once."""
    settings = _settings(tmp_path)
    doc_a = _persist_pending_committed(
        processing_session_factory,
        tmp_path,
        suffix="depth-a",
        text=("depth alpha corpus " * 12).strip(),
    )
    doc_b = _persist_pending_committed(
        processing_session_factory,
        tmp_path,
        suffix="depth-b",
        text=("depth beta corpus " * 12).strip(),
    )

    depth = 0
    max_depth = 0
    depth_guard = threading.Lock()
    original_locked = HrDocumentProcessingService._replace_chunks_and_index_locked

    def counting_locked(self, session, document, drafts):
        nonlocal depth, max_depth
        with depth_guard:
            depth += 1
            max_depth = max(max_depth, depth)
        try:
            return original_locked(self, session, document, drafts)
        finally:
            with depth_guard:
                depth -= 1

    def run(document_id: str) -> None:
        HrDocumentProcessingService(
            processing_session_factory,
            settings,
            embedding_provider=MockEncoder(),
        ).process(document_id)

    with patch.object(
        HrDocumentProcessingService,
        "_replace_chunks_and_index_locked",
        counting_locked,
    ):
        t1 = threading.Thread(target=run, args=(doc_a,))
        t2 = threading.Thread(target=run, args=(doc_b,))
        t1.start()
        t2.start()
        t1.join(timeout=120)
        t2.join(timeout=120)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert max_depth == 1


def test_concurrent_indexing_both_documents_in_bm25(
    processing_session_factory: sessionmaker[Session],
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    doc_a = _persist_pending_committed(
        processing_session_factory,
        tmp_path,
        suffix="bm25-a",
        text="unique alpha token for concurrent bm25",
    )
    doc_b = _persist_pending_committed(
        processing_session_factory,
        tmp_path,
        suffix="bm25-b",
        text="unique beta token for concurrent bm25",
    )

    def run(document_id: str) -> None:
        HrDocumentProcessingService(
            processing_session_factory,
            settings,
            embedding_provider=MockEncoder(),
        ).process(document_id)

    t1 = threading.Thread(target=run, args=(doc_a,))
    t2 = threading.Thread(target=run, args=(doc_b,))
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert not t1.is_alive()
    assert not t2.is_alive()

    ready_alpha, alpha_hits = search_chunks_by_query(
        "alpha token",
        settings=settings,
        top_k=5,
    )
    ready_beta, beta_hits = search_chunks_by_query(
        "beta token",
        settings=settings,
        top_k=5,
    )
    assert ready_alpha is True
    assert ready_beta is True
    assert alpha_hits
    assert beta_hits


def test_bm25_failure_does_not_rollback_mysql_faiss(
    db_session: Session,
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    doc_id = _persist_pending(db_session, tmp_path, suffix="bm25-fail", text=("rollback check body " * 15).strip())
    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        settings,
        embedding_provider=MockEncoder(),
    )
    with patch(
        "smarthris.services.hr_document_processing_service.rebuild_persisted_bm25_from_records",
        side_effect=RuntimeError("bm25 rebuild failed"),
    ):
        service.process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.status == HrDocumentStatus.INDEXED