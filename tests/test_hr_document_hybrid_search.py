"""Phase 5.7 — hybrid BM25 + RRF document search tests."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import ValidationError
from smarthris.models.hr_document import HrDocumentStatus, HrDocumentType
from smarthris.rag.bm25_store import (
    rebuild_persisted_bm25_from_records,
    reset_bm25_cache_for_tests,
    search_chunks_by_query,
    tokenize_for_bm25,
)
from smarthris.rag.embeddings import DocumentProcessingError, reset_embedding_cache_for_tests
from smarthris.rag.faiss_search import reset_search_index_cache_for_tests
from smarthris.rag.faiss_store import ChunkVectorRecord
from smarthris.rag.rrf import reciprocal_rank_fusion
from smarthris.schemas.hr_document import HrDocumentRetrievalMode, HrDocumentSearchRequest
from smarthris.services.hr_document_processing_service import (
    HrDocumentProcessingService,
    reconcile_vector_index_from_database,
)
from smarthris.services.hr_document_retrieval_service import HrDocumentRetrievalService

from tests.test_hr_document_processing import MockEncoder, _TrackingSessionFactory, _settings
from tests.test_hr_document_search import (
    _build_index,
    _seed_indexed_chunk,
    search_api_client,
)


class FakeBM25Okapi:
    """Test double for rank_bm25.BM25Okapi (token overlap scores)."""

    def __init__(self, corpus: list[list[str]]) -> None:
        self._corpus = corpus

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        query_set = set(query_tokens)
        scores: list[float] = []
        for doc_tokens in self._corpus:
            scores.append(float(len(query_set.intersection(doc_tokens))))
        return scores


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    reset_embedding_cache_for_tests()
    reset_search_index_cache_for_tests()
    reset_bm25_cache_for_tests()


@pytest.fixture(autouse=True)
def _fake_bm25_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "smarthris.rag.bm25_store.build_bm25_okapi",
        lambda corpus: FakeBM25Okapi(corpus),
    )


def test_tokenize_for_bm25() -> None:
    assert tokenize_for_bm25("Leave-policy 2024") == ["leave", "policy", "2024"]


def test_bm25_persist_load_and_search(tmp_path) -> None:
    records = [
        ChunkVectorRecord(chunk_id=1, chunk_text="annual leave carryover"),
        ChunkVectorRecord(chunk_id=2, chunk_text="dental benefits plan"),
    ]
    settings = _settings(tmp_path)
    rebuild_persisted_bm25_from_records(records, settings=settings)
    ready, hits = search_chunks_by_query(
        "carryover leave",
        settings=settings,
        top_k=5,
    )
    assert ready is True
    assert hits[0].chunk_id == 1
    assert hits[0].score >= hits[1].score


def test_bm25_empty_corpus(tmp_path) -> None:
    settings = _settings(tmp_path)
    rebuild_persisted_bm25_from_records([], settings=settings)
    ready, hits = search_chunks_by_query("anything", settings=settings, top_k=3)
    assert ready is True
    assert hits == []


def test_bm25_missing_index(tmp_path) -> None:
    settings = _settings(tmp_path)
    ready, hits = search_chunks_by_query("x", settings=settings, top_k=3)
    assert ready is False
    assert hits == []


def test_bm25_incompatible_manifest(tmp_path) -> None:
    settings = _settings(tmp_path)
    live = settings.rag_index_dir
    bm25_dir = f"{live}/bm25"
    from pathlib import Path

    path = Path(bm25_dir)
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("bm25_manifest.json").write_text(
        '{"format_version": 999, "chunk_count": 1, "updated_at": "x"}',
        encoding="utf-8",
    )
    path.joinpath("corpus.json").write_text("[]", encoding="utf-8")
    ready, _ = search_chunks_by_query("x", settings=settings, top_k=2)
    assert ready is False


def test_rrf_known_ranking() -> None:
    fused = reciprocal_rank_fusion([10, 20], [20, 10], k=60)
    assert fused[0][0] in {10, 20}
    assert len(fused) == 2
    # chunk present in both lists at rank 2 and rank 2 => equal RRF contributions
    scores = dict(fused)
    assert scores[10] == scores[20]


def test_rrf_deduplication_and_tie_break() -> None:
    fused = reciprocal_rank_fusion([5, 3, 7], [], k=1)
    assert [cid for cid, _ in fused] == [5, 3, 7]


def test_hybrid_chunk_in_both_lists_scores_higher(tmp_path) -> None:
    shared = 42
    fused = reciprocal_rank_fusion([shared, 9], [8, shared], k=60)
    scores = dict(fused)
    assert scores[shared] > scores[9]
    assert scores[shared] > scores[8]


def test_semantic_mode_regression(db_session: Session, tmp_path) -> None:
    text = "semantic regression chunk"
    doc_id = "00000000-0000-4000-8000-000000000701"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=701, chunk_text=text)
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=701, chunk_text=text)])
    service = HrDocumentRetrievalService(db_session, settings, embedding_provider=MockEncoder())
    response = service.search(
        HrDocumentSearchRequest(query="chunk", retrieval_mode=HrDocumentRetrievalMode.SEMANTIC),
    )
    assert response.retrieval_mode == HrDocumentRetrievalMode.SEMANTIC
    assert response.index_ready is True
    assert len(response.items) == 1


def test_keyword_mode_without_faiss(db_session: Session, tmp_path) -> None:
    text = "keyword only annual leave"
    doc_id = "00000000-0000-4000-8000-000000000801"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=801, chunk_text=text)
    settings = _settings(tmp_path)
    rebuild_persisted_bm25_from_records(
        [ChunkVectorRecord(chunk_id=801, chunk_text=text)],
        settings=settings,
    )
    service = HrDocumentRetrievalService(db_session, settings, embedding_provider=MockEncoder())
    response = service.search(
        HrDocumentSearchRequest(query="annual leave", retrieval_mode=HrDocumentRetrievalMode.KEYWORD),
    )
    assert response.retrieval_mode == HrDocumentRetrievalMode.KEYWORD
    assert response.index_ready is True
    assert response.items[0].chunk_id == 801


def test_hybrid_mode(db_session: Session, tmp_path) -> None:
    text = "hybrid annual leave policy"
    doc_id = "00000000-0000-4000-8000-000000000901"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=901, chunk_text=text)
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=901, chunk_text=text)])
    rebuild_persisted_bm25_from_records(
        [ChunkVectorRecord(chunk_id=901, chunk_text=text)],
        settings=settings,
    )
    service = HrDocumentRetrievalService(db_session, settings, embedding_provider=MockEncoder())
    response = service.search(
        HrDocumentSearchRequest(query="annual", retrieval_mode=HrDocumentRetrievalMode.HYBRID),
    )
    assert response.retrieval_mode == HrDocumentRetrievalMode.HYBRID
    assert response.index_ready is True
    assert len(response.items) >= 1


def test_stale_chunk_and_non_indexed_filtered(db_session: Session, tmp_path) -> None:
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=1000, chunk_text="orphan")])
    rebuild_persisted_bm25_from_records(
        [ChunkVectorRecord(chunk_id=1000, chunk_text="orphan")],
        settings=settings,
    )
    doc_id = "00000000-0000-4000-8000-000000001001"
    _seed_indexed_chunk(
        db_session,
        doc_id=doc_id,
        chunk_id=1001,
        chunk_text="valid",
        status=HrDocumentStatus.FAILED,
    )
    service = HrDocumentRetrievalService(db_session, settings, embedding_provider=MockEncoder())
    response = service.search(
        HrDocumentSearchRequest(query="orphan", retrieval_mode=HrDocumentRetrievalMode.HYBRID),
    )
    assert all(item.chunk_id != 1001 for item in response.items)


def test_doc_type_filter(db_session: Session, tmp_path) -> None:
    text = "filter me"
    doc_id = "00000000-0000-4000-8000-000000001101"
    _seed_indexed_chunk(
        db_session,
        doc_id=doc_id,
        chunk_id=1101,
        chunk_text=text,
        doc_type=HrDocumentType.POLICY,
    )
    settings = _settings(tmp_path)
    rebuild_persisted_bm25_from_records(
        [ChunkVectorRecord(chunk_id=1101, chunk_text=text)],
        settings=settings,
    )
    service = HrDocumentRetrievalService(db_session, settings, embedding_provider=MockEncoder())
    response = service.search(
        HrDocumentSearchRequest(
            query="filter",
            retrieval_mode=HrDocumentRetrievalMode.KEYWORD,
            doc_type=HrDocumentType.HANDBOOK,
        ),
    )
    assert response.items == []


def test_candidates_must_cover_top_k(db_session: Session, tmp_path) -> None:
    settings = _settings(tmp_path)
    settings = settings.model_copy(update={"rag_retrieval_candidates": 2})
    service = HrDocumentRetrievalService(db_session, settings, embedding_provider=MockEncoder())
    with pytest.raises(ValidationError, match="rag_retrieval_candidates"):
        service.search(HrDocumentSearchRequest(query="ok", top_k=5))


def test_processing_rebuilds_bm25(db_session: Session, tmp_path) -> None:
    from io import BytesIO

    from smarthris.rag.storage import save_document_upload
    from smarthris.repositories.hr_document_repository import HrDocumentRepository
    from smarthris.models.hr_document import HrDocument, HrDocumentType

    text = ("processing rebuild text " * 8).strip()
    upload = save_document_upload(
        source=BytesIO(text.encode()),
        rag_document_dir=tmp_path / "documents",
        original_filename="policy.txt",
    )
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=upload.document_id,
            original_filename="policy.txt",
            content_type="text/plain",
            checksum="h7" * 32,
            storage_path=str(upload.storage_path),
            doc_type=HrDocumentType.POLICY,
            status=HrDocumentStatus.PENDING,
        )
    )
    db_session.flush()
    settings = _settings(tmp_path)
    service = HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        settings,
        embedding_provider=MockEncoder(),
    )
    service.process(upload.document_id)
    ready, hits = search_chunks_by_query("rebuild", settings=settings, top_k=5)
    assert ready is True
    assert len(hits) >= 1


def test_reconcile_rebuilds_both_indices(db_session: Session, tmp_path) -> None:
    text = "reconcile both"
    doc_id = "00000000-0000-4000-8000-000000001201"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=1201, chunk_text=text)
    settings = _settings(tmp_path)
    reconcile_vector_index_from_database(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
    )
    ready, _ = search_chunks_by_query("reconcile", settings=settings, top_k=3)
    assert ready is True


def test_api_default_semantic(search_api_client: TestClient) -> None:
    response = search_api_client.post("/documents/search", json={"query": "default mode"})
    assert response.status_code == 200
    assert response.json()["retrieval_mode"] == "semantic"


def test_api_hybrid_response_no_storage_path(
    search_api_client: TestClient,
    db_session: Session,
    tmp_path,
) -> None:
    text = "api hybrid snippet"
    doc_id = "00000000-0000-4000-8000-000000001301"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=1301, chunk_text=text)
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=1301, chunk_text=text)])
    rebuild_persisted_bm25_from_records(
        [ChunkVectorRecord(chunk_id=1301, chunk_text=text)],
        settings=settings,
    )
    response = search_api_client.post(
        "/documents/search",
        json={"query": "snippet", "retrieval_mode": "hybrid"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "storage_path" not in str(body)
    assert body["retrieval_mode"] == "hybrid"


def test_rank_bm25_missing_raises_document_processing_error(tmp_path) -> None:
    settings = _settings(tmp_path)

    def _raise_import():
        raise DocumentProcessingError("BM25 keyword search requires the rank-bm25 package")

    with patch("smarthris.rag.bm25_store.build_bm25_okapi", side_effect=_raise_import):
        with pytest.raises(DocumentProcessingError, match="Failed to rebuild BM25"):
            rebuild_persisted_bm25_from_records(
                [ChunkVectorRecord(chunk_id=1, chunk_text="x")],
                settings=settings,
            )