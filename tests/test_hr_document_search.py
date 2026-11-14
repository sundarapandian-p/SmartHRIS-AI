"""Phase 5.6 — HR document semantic search tests."""

from __future__ import annotations

import io
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.core.exceptions import ValidationError
from smarthris.main import create_app
from smarthris.models.hr_document import (
    HrDocument,
    HrDocumentStatus,
    HrDocumentType,
    HrDocumentVisibility,
)
from smarthris.models.hr_document_chunk import HrDocumentChunk
from smarthris.rag.embeddings import DocumentProcessingError, reset_embedding_cache_for_tests
from smarthris.rag.bm25_store import reset_bm25_cache_for_tests
from smarthris.rag.faiss_search import reset_search_index_cache_for_tests, search_chunks_by_vector
from smarthris.rag.faiss_store import ChunkVectorRecord, rebuild_index_from_records
from smarthris.rag.storage import save_document_upload
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.schemas.hr_document import HrDocumentSearchRequest
from smarthris.services.hr_document_retrieval_service import HrDocumentRetrievalService

from tests.auth_helpers import TEST_JWT_SECRET, attach_active_user_override
from tests.test_hr_document_processing import MockEncoder, _settings

ALL_DOCUMENT_VISIBILITIES = frozenset(
    {HrDocumentVisibility.HR_ONLY, HrDocumentVisibility.ALL_STAFF}
)


@pytest.fixture(autouse=True)
def _reset_rag_caches() -> None:
    reset_embedding_cache_for_tests()
    reset_search_index_cache_for_tests()
    reset_bm25_cache_for_tests()


def _settings_search(tmp_path) -> Settings:
    return _settings(tmp_path)


def _build_index(tmp_path, records: list[ChunkVectorRecord], *, dimension: int = 8) -> Settings:
    settings = _settings_search(tmp_path)

    def embed(texts):
        vectors = np.zeros((len(texts), dimension), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i, i % dimension] = 1.0
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return vectors / norms

    rebuild_index_from_records(
        records,
        settings=settings,
        dimension=dimension,
        embed_fn=embed,
    )
    return settings


def _seed_indexed_chunk(
    db_session: Session,
    *,
    doc_id: str,
    chunk_id: int,
    chunk_text: str,
    status: HrDocumentStatus = HrDocumentStatus.INDEXED,
    doc_type: HrDocumentType = HrDocumentType.POLICY,
    title: str | None = "Policy Title",
    storage_path: str = "/private/policy.txt",
    visibility: HrDocumentVisibility = HrDocumentVisibility.HR_ONLY,
) -> None:
    HrDocumentRepository(db_session).add(
        HrDocument(
            id=doc_id,
            original_filename="leave-policy.txt",
            content_type="text/plain",
            checksum=f"{chunk_id}" * 4,
            storage_path=storage_path,
            title=title,
            doc_type=doc_type,
            visibility=visibility,
            status=status,
        )
    )
    chunk = HrDocumentChunk(
        id=chunk_id,
        document_id=doc_id,
        chunk_index=0,
        chunk_text=chunk_text,
        page_number=2,
        section_title="Carryover",
        token_count=3,
    )
    db_session.add(chunk)
    db_session.flush()


@pytest.fixture
def search_api_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path,
    hr_test_user,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Search",
        database_url=integration_database_url,
        rag_document_dir=str(tmp_path / "documents"),
        rag_index_dir=str(tmp_path / "indexes"),
        rag_top_k=5,
        max_upload_bytes=1024,
        embedding_model_name="unit-test-model",
        jwt_secret_key=TEST_JWT_SECRET,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, hr_test_user)

    def _encode(texts, *, settings, provider=None, batch_size=32):
        encoder = provider or MockEncoder()
        return encoder.encode(list(texts))

    encode_patch = patch(
        "smarthris.services.hr_document_retrieval_service.encode_texts",
        side_effect=_encode,
    )
    dimension_patch = patch(
        "smarthris.services.hr_document_retrieval_service.embedding_dimension",
        return_value=8,
    )
    with patch("smarthris.api.routers.documents.process_hr_document"):
        with encode_patch, dimension_patch:
            with TestClient(app) as client:
                yield client
    app.dependency_overrides.clear()


def test_faiss_search_returns_ranked_hits(tmp_path) -> None:
    settings = _build_index(
        tmp_path,
        [ChunkVectorRecord(chunk_id=10, chunk_text="a"), ChunkVectorRecord(chunk_id=11, chunk_text="b")],
    )
    query = np.zeros(8, dtype=np.float32)
    query[0] = 1.0
    ready, hits = search_chunks_by_vector(
        query,
        settings=settings,
        dimension=8,
        top_k=2,
    )
    assert ready is True
    assert len(hits) == 2
    assert hits[0].chunk_id == 10
    assert hits[0].score >= hits[1].score


def test_faiss_search_empty_index(tmp_path) -> None:
    settings = _settings_search(tmp_path)
    settings.rag_index_dir = str(tmp_path / "indexes-empty")
    query = np.zeros(8, dtype=np.float32)
    query[0] = 1.0
    ready, hits = search_chunks_by_vector(
        query,
        settings=settings,
        dimension=8,
        top_k=5,
    )
    assert ready is False
    assert hits == []


def test_faiss_search_top_k_exceeds_ntotal(tmp_path) -> None:
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=99, chunk_text="only one")])
    query = np.zeros(8, dtype=np.float32)
    query[0] = 1.0
    ready, hits = search_chunks_by_vector(query, settings=settings, dimension=8, top_k=50)
    assert ready is True
    assert len(hits) == 1


def test_faiss_search_index_with_zero_vectors(tmp_path) -> None:
    settings = _build_index(tmp_path, [])
    query = np.zeros(8, dtype=np.float32)
    query[0] = 1.0
    ready, hits = search_chunks_by_vector(query, settings=settings, dimension=8, top_k=5)
    assert ready is True
    assert hits == []


def test_faiss_search_missing_index_directory(tmp_path) -> None:
    settings = _settings_search(tmp_path)
    query = np.zeros(8, dtype=np.float32)
    ready, hits = search_chunks_by_vector(query, settings=settings, dimension=8, top_k=3)
    assert ready is False
    assert hits == []


def test_repository_ignores_non_indexed_documents(db_session: Session) -> None:
    _seed_indexed_chunk(
        db_session,
        doc_id="00000000-0000-4000-8000-000000000101",
        chunk_id=101,
        chunk_text="hidden",
        status=HrDocumentStatus.FAILED,
    )
    mapped = HrDocumentChunkRepository(db_session).map_indexed_chunks_by_ids([101])
    assert mapped == {}


def test_repository_doc_type_filter(db_session: Session) -> None:
    doc_id = "00000000-0000-4000-8000-000000000102"
    _seed_indexed_chunk(
        db_session,
        doc_id=doc_id,
        chunk_id=102,
        chunk_text="policy chunk",
        doc_type=HrDocumentType.POLICY,
    )
    repo = HrDocumentChunkRepository(db_session)
    assert 102 in repo.map_indexed_chunks_by_ids([102], doc_type=HrDocumentType.POLICY)
    assert repo.map_indexed_chunks_by_ids([102], doc_type=HrDocumentType.HANDBOOK) == {}


def test_retrieval_stale_chunk_ids(db_session: Session, tmp_path) -> None:
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=200, chunk_text="orphan vector")])
    doc_id = "00000000-0000-4000-8000-000000000200"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=201, chunk_text="real chunk")
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    response = service.search(HrDocumentSearchRequest(query="probe"))
    assert response.index_ready is True
    assert response.items == []


def test_retrieval_title_fallback(db_session: Session, tmp_path) -> None:
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=301, chunk_text="carryover text")])
    doc_id = "00000000-0000-4000-8000-000000000301"
    _seed_indexed_chunk(
        db_session,
        doc_id=doc_id,
        chunk_id=301,
        chunk_text="carryover text",
        title=None,
    )
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    with patch(
        "smarthris.services.hr_document_retrieval_service.search_chunks_by_vector",
        return_value=(True, [type("Hit", (), {"chunk_id": 301, "score": 0.88})()]),
    ):
        response = service.search(HrDocumentSearchRequest(query="carryover"))
    assert response.items[0].document_title == "leave-policy.txt"


def test_retrieval_query_validation(db_session: Session, tmp_path) -> None:
    settings = _settings_search(tmp_path)
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    with pytest.raises(ValidationError, match="must not be empty"):
        service.search(HrDocumentSearchRequest(query="   "))


def test_retrieval_top_k_validation(db_session: Session, tmp_path) -> None:
    settings = _settings_search(tmp_path)
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    payload = HrDocumentSearchRequest.model_construct(query="ok", top_k=21, doc_type=None)
    with pytest.raises(ValidationError, match="top_k must be between"):
        service.search(payload)


def test_retrieval_missing_index(db_session: Session, tmp_path) -> None:
    settings = _settings_search(tmp_path)
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    response = service.search(HrDocumentSearchRequest(query="anything"))
    assert response.index_ready is False
    assert response.items == []


def test_retrieval_embedding_failure(db_session: Session, tmp_path) -> None:
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=401, chunk_text="x")])
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    with patch(
        "smarthris.services.hr_document_retrieval_service.encode_texts",
        side_effect=DocumentProcessingError("embedding failed"),
    ):
        with pytest.raises(ValidationError, match="temporarily unavailable"):
            service.search(HrDocumentSearchRequest(query="fail"))


def test_retrieval_fail_closed_without_visibility_allowlist(
    db_session: Session,
    tmp_path,
) -> None:
    chunk_text = "secret HR compensation grid"
    _seed_indexed_chunk(
        db_session,
        doc_id="00000000-0000-4000-8000-000000009901",
        chunk_id=9901,
        chunk_text=chunk_text,
        visibility=HrDocumentVisibility.HR_ONLY,
    )
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=9901, chunk_text=chunk_text)])
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=MockEncoder(),
    )
    with patch(
        "smarthris.services.hr_document_retrieval_service.search_chunks_by_vector",
        return_value=(True, [type("Hit", (), {"chunk_id": 9901, "score": 0.99})()]),
    ):
        response = service.search(HrDocumentSearchRequest(query="compensation"))
    assert response.index_ready is True
    assert response.items == []


def test_retrieval_end_to_end(db_session: Session, tmp_path) -> None:
    chunk_text = "annual leave carryover policy details"
    doc_id = "00000000-0000-4000-8000-000000000501"
    _seed_indexed_chunk(
        db_session,
        doc_id=doc_id,
        chunk_id=501,
        chunk_text=chunk_text,
        title="Leave Policy",
    )
    settings = _build_index(tmp_path, [ChunkVectorRecord(chunk_id=501, chunk_text=chunk_text)])

    encoder = MockEncoder()
    service = HrDocumentRetrievalService(
        db_session,
        settings,
        embedding_provider=encoder,
        allowed_visibilities=ALL_DOCUMENT_VISIBILITIES,
    )
    response = service.search(HrDocumentSearchRequest(query="carryover"))
    assert response.index_ready is True
    assert len(response.items) == 1
    item = response.items[0]
    assert item.rank == 1
    assert item.chunk_id == 501
    assert item.document_title == "Leave Policy"
    assert item.chunk_text == chunk_text
    assert item.page_number == 2
    assert item.section_title == "Carryover"


def test_api_search_response_structure(
    search_api_client: TestClient,
    db_session: Session,
    tmp_path,
) -> None:
    chunk_text = "searchable policy snippet"
    doc_id = "00000000-0000-4000-8000-000000000601"
    _seed_indexed_chunk(db_session, doc_id=doc_id, chunk_id=601, chunk_text=chunk_text)
    _build_index(tmp_path, [ChunkVectorRecord(chunk_id=601, chunk_text=chunk_text)])

    response = search_api_client.post(
        "/documents/search",
        json={"query": "policy", "top_k": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["index_ready"] is True
    assert len(body["items"]) >= 1
    dumped = str(body)
    assert "storage_path" not in dumped
    assert body["items"][0]["chunk_text"] == chunk_text


def test_api_search_empty_query(search_api_client: TestClient) -> None:
    response = search_api_client.post("/documents/search", json={"query": "   "})
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_api_search_missing_index(search_api_client: TestClient) -> None:
    response = search_api_client.post("/documents/search", json={"query": "no index yet"})
    assert response.status_code == 200
    body = response.json()
    assert body["index_ready"] is False
    assert body["items"] == []


def test_api_embedding_failure_returns_400(search_api_client: TestClient, tmp_path) -> None:
    _build_index(tmp_path, [ChunkVectorRecord(chunk_id=701, chunk_text="y")])
    with patch(
        "smarthris.services.hr_document_retrieval_service.encode_texts",
        side_effect=DocumentProcessingError("boom"),
    ):
        response = search_api_client.post("/documents/search", json={"query": "fail"})
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"


def test_existing_routes_still_work(search_api_client: TestClient) -> None:
    created = search_api_client.post(
        "/documents",
        files={"file": ("still-works.txt", b"route check\n", "text/plain")},
    )
    assert created.status_code == 201
    doc_id = created.json()["id"]
    assert search_api_client.get(f"/documents/{doc_id}").status_code == 200
    assert search_api_client.get("/documents").status_code == 200