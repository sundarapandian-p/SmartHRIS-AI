"""RBAC: employees must not retrieve hr_only content via search or RAG."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.main import create_app
from smarthris.models.hr_document import HrDocumentVisibility
from smarthris.rag.bm25_store import reset_bm25_cache_for_tests
from smarthris.rag.embeddings import reset_embedding_cache_for_tests
from smarthris.rag.faiss_search import reset_search_index_cache_for_tests
from smarthris.rag.faiss_store import ChunkVectorRecord
from smarthris.rag.ollama_client import OllamaChatClient
from smarthris.schemas.hr_document import HrDocumentRetrievalMode
from tests.auth_helpers import TEST_JWT_SECRET, bearer_headers
from tests.test_hr_document_processing import MockEncoder
from tests.test_hr_document_search import _build_index, _seed_indexed_chunk


@pytest.fixture(autouse=True)
def _reset_rag_caches() -> None:
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


@pytest.fixture
def employee_auth_headers(employee_test_user) -> dict[str, str]:
    return bearer_headers(employee_test_user)


@pytest.fixture
def visibility_search_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        rag_document_dir=str(tmp_path / "documents"),
        rag_index_dir=str(tmp_path / "indexes"),
        rag_top_k=5,
        embedding_model_name="unit-test-model",
        jwt_secret_key=TEST_JWT_SECRET,
    )
    app = create_app(settings)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

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
    with encode_patch, dimension_patch:
        with TestClient(app) as client:
            yield client
    app.dependency_overrides.clear()


def _seed_pair(db_session: Session) -> tuple[int, int]:
    hr_only_id = 8801
    staff_id = 8802
    _seed_indexed_chunk(
        db_session,
        doc_id="00000000-0000-4000-8000-000000008801",
        chunk_id=hr_only_id,
        chunk_text="confidential HR salary band guidance",
        visibility=HrDocumentVisibility.HR_ONLY,
    )
    _seed_indexed_chunk(
        db_session,
        doc_id="00000000-0000-4000-8000-000000008802",
        chunk_id=staff_id,
        chunk_text="twenty days annual leave for all staff",
        visibility=HrDocumentVisibility.ALL_STAFF,
    )
    return hr_only_id, staff_id


def test_employee_hybrid_search_excludes_hr_only(
    visibility_search_client: TestClient,
    employee_auth_headers: dict[str, str],
    db_session: Session,
    tmp_path,
) -> None:
    hr_only_id, staff_id = _seed_pair(db_session)
    _build_index(
        tmp_path,
        [
            ChunkVectorRecord(chunk_id=hr_only_id, chunk_text="confidential HR salary band guidance"),
            ChunkVectorRecord(chunk_id=staff_id, chunk_text="twenty days annual leave for all staff"),
        ],
    )
    response = visibility_search_client.post(
        "/documents/search",
        headers=employee_auth_headers,
        json={"query": "leave", "retrieval_mode": HrDocumentRetrievalMode.HYBRID.value},
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert items
    assert all("salary" not in (item.get("chunk_text") or "").lower() for item in items)
    assert any("annual leave" in (item.get("chunk_text") or "").lower() for item in items)


def test_employee_rag_does_not_cite_hr_only(
    visibility_search_client: TestClient,
    employee_auth_headers: dict[str, str],
    db_session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hr_only_id, staff_id = _seed_pair(db_session)
    _build_index(
        tmp_path,
        [
            ChunkVectorRecord(chunk_id=hr_only_id, chunk_text="confidential HR salary band guidance"),
            ChunkVectorRecord(chunk_id=staff_id, chunk_text="twenty days annual leave for all staff"),
        ],
    )

    captured_bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(request.content.decode())
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "You get [S1] twenty days annual leave."}},
        )

    monkeypatch.setattr(
        "smarthris.services.hr_document_qa_service.OllamaChatClient",
        lambda s, **kw: OllamaChatClient(s, transport=httpx.MockTransport(handler)),
    )

    response = visibility_search_client.post(
        "/rag/query",
        headers=employee_auth_headers,
        json={"question": "How much leave?", "retrieval_mode": HrDocumentRetrievalMode.HYBRID.value},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "salary" not in body.get("answer", "").lower()
    for source in body.get("sources", []):
        assert "salary" not in (source.get("excerpt") or "").lower()
    assert captured_bodies
    assert "salary" not in captured_bodies[0].lower()


@pytest.mark.parametrize(
    "retrieval_mode",
    [
        HrDocumentRetrievalMode.SEMANTIC.value,
        HrDocumentRetrievalMode.KEYWORD.value,
    ],
)
def test_employee_search_excludes_hr_only_by_mode(
    visibility_search_client: TestClient,
    employee_auth_headers: dict[str, str],
    db_session: Session,
    tmp_path,
    retrieval_mode: str,
) -> None:
    hr_only_id, staff_id = _seed_pair(db_session)
    _build_index(
        tmp_path,
        [
            ChunkVectorRecord(chunk_id=hr_only_id, chunk_text="confidential HR salary band guidance"),
            ChunkVectorRecord(chunk_id=staff_id, chunk_text="twenty days annual leave for all staff"),
        ],
    )
    response = visibility_search_client.post(
        "/documents/search",
        headers=employee_auth_headers,
        json={"query": "leave", "retrieval_mode": retrieval_mode},
    )
    assert response.status_code == 200, response.text
    for item in response.json()["items"]:
        assert "salary" not in (item.get("chunk_text") or "").lower()


@pytest.mark.parametrize(
    "retrieval_mode",
    [
        HrDocumentRetrievalMode.SEMANTIC.value,
        HrDocumentRetrievalMode.KEYWORD.value,
    ],
)
def test_employee_rag_excludes_hr_only_by_mode(
    visibility_search_client: TestClient,
    employee_auth_headers: dict[str, str],
    db_session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    retrieval_mode: str,
) -> None:
    hr_only_id, staff_id = _seed_pair(db_session)
    _build_index(
        tmp_path,
        [
            ChunkVectorRecord(chunk_id=hr_only_id, chunk_text="confidential HR salary band guidance"),
            ChunkVectorRecord(chunk_id=staff_id, chunk_text="twenty days annual leave for all staff"),
        ],
    )

    captured_bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_bodies.append(request.content.decode())
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "Annual leave applies [S1]."}},
        )

    monkeypatch.setattr(
        "smarthris.services.hr_document_qa_service.OllamaChatClient",
        lambda s, **kw: OllamaChatClient(s, transport=httpx.MockTransport(handler)),
    )

    response = visibility_search_client.post(
        "/rag/query",
        headers=employee_auth_headers,
        json={"question": "leave policy", "retrieval_mode": retrieval_mode},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    for source in body.get("sources", []):
        assert "salary" not in (source.get("excerpt") or "").lower()
    if captured_bodies:
        assert "salary" not in captured_bodies[0].lower()