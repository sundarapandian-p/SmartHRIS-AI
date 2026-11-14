"""Phase 5.10.2 — Full HR document RAG pipeline integration (isolated tmp indexes)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.main import create_app
from smarthris.models.hr_document import HrDocumentStatus
from smarthris.rag.bm25_store import bm25_manifest_path, load_bm25_manifest, reset_bm25_cache_for_tests
from smarthris.rag.embeddings import reset_embedding_cache_for_tests
from smarthris.rag.faiss_search import reset_search_index_cache_for_tests
from smarthris.rag.faiss_store import load_manifest, manifest_path, model_index_dir
from smarthris.rag.ollama_client import OllamaChatClient
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.schemas.hr_document import HrDocumentRetrievalMode
from smarthris.schemas.rag import RagAnswerStatus
from smarthris.services.hr_document_processing_service import HrDocumentProcessingService

from smarthris.models.user import UserRole
from tests.auth_helpers import TEST_JWT_SECRET, attach_active_user_override, create_test_user
from tests.test_db_guard import assert_safe_integration_database_url
from tests.test_hr_document_processing import MockEncoder, _TrackingSessionFactory, _settings


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


@pytest.fixture(autouse=True)
def _enforce_integration_database_guard(integration_database_url: str) -> None:
    assert_safe_integration_database_url(integration_database_url)


def _pipeline_settings(tmp_path, integration_database_url: str) -> Settings:
    base = _settings(tmp_path)
    return base.model_copy(
        update={
            "database_url": integration_database_url,
            "max_upload_bytes": 256_000,
            "rag_top_k": 5,
            "jwt_secret_key": TEST_JWT_SECRET,
        }
    )


def _encode_stub(texts, *, settings, provider=None, batch_size=32):
    encoder = provider or MockEncoder()
    return encoder.encode(list(texts))


@pytest.fixture
def pipeline_api_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path,
    hr_test_user,
) -> TestClient:
    """TestClient: real upload background processing, test DB session, tmp RAG paths."""
    settings = _pipeline_settings(tmp_path, integration_database_url)
    app = create_app(settings)
    app.state.db_session_factory = _TrackingSessionFactory(db_session)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, hr_test_user)

    encode_targets = (
        "smarthris.services.hr_document_retrieval_service.encode_texts",
        "smarthris.services.hr_document_processing_service.encode_texts",
    )
    dimension_targets = (
        "smarthris.services.hr_document_retrieval_service.embedding_dimension",
        "smarthris.services.hr_document_processing_service.embedding_dimension",
    )
    patches = [
        patch(target, side_effect=_encode_stub)
        for target in encode_targets
    ] + [
        patch(target, return_value=8)
        for target in dimension_targets
    ]
    for item in patches:
        item.start()
    try:
        with TestClient(app) as client:
            client._pipeline_settings = settings  # type: ignore[attr-defined]
            yield client
    finally:
        for item in patches:
            item.stop()
        app.dependency_overrides.clear()


def test_full_pipeline_upload_process_retrieve_and_qa(
    pipeline_api_client: TestClient,
    db_session: Session,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings: Settings = pipeline_api_client._pipeline_settings  # type: ignore[attr-defined]
    run_id = uuid.uuid4().hex[:12]
    policy_phrase = "twenty-six days of compassionate leave"
    policy_body = (
        f"INTEGRATION-5-10-2-{run_id}: All staff receive {policy_phrase} per fiscal year. "
        f"Contact HR for eligibility. "
    ) * 8
    expected_answer = f"Staff receive {policy_phrase} [S1]."

    with patch("smarthris.api.routers.documents.process_hr_document") as schedule_processing:
        upload = pipeline_api_client.post(
            "/documents",
            files={
                "file": (
                    f"compassionate-leave-{run_id}.txt",
                    policy_body.encode("utf-8"),
                    "text/plain",
                )
            },
            data={"title": f"Compassionate Leave {run_id}", "doc_type": "policy"},
        )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert "storage_path" not in body
    doc_id = body["id"]
    assert body["status"] == HrDocumentStatus.PENDING.value
    schedule_processing.assert_called_once()
    _args, schedule_kwargs = schedule_processing.call_args
    assert _args[0] == doc_id
    assert schedule_kwargs["settings"] is settings

    HrDocumentProcessingService(
        _TrackingSessionFactory(db_session),
        settings,
        embedding_provider=MockEncoder(),
    ).process(doc_id)

    document = HrDocumentRepository(db_session).get_by_id(doc_id)
    assert document is not None
    assert document.original_filename == f"compassionate-leave-{run_id}.txt"
    assert document.status == HrDocumentStatus.INDEXED

    chunks = HrDocumentChunkRepository(db_session).list_by_document_id(doc_id)
    assert len(chunks) >= 1
    assert any(policy_phrase in chunk.chunk_text for chunk in chunks)

    assert load_manifest(manifest_path(settings)) is not None
    assert (model_index_dir(settings) / "index.faiss").is_file()
    bm25_manifest = load_bm25_manifest(bm25_manifest_path(settings))
    assert bm25_manifest is not None
    assert bm25_manifest.chunk_count >= 1

    search = pipeline_api_client.post(
        "/documents/search",
        json={
            "query": "How many compassionate leave days?",
            "retrieval_mode": HrDocumentRetrievalMode.HYBRID.value,
        },
    )
    assert search.status_code == 200, search.text
    search_body = search.json()
    assert search_body["index_ready"] is True
    assert search_body["retrieval_mode"] == "hybrid"
    assert search_body["items"]
    assert any(item["document_id"] == doc_id for item in search_body["items"])
    assert any(
        policy_phrase.split()[0] in item.get("chunk_text", "").lower()
        or policy_phrase in item.get("chunk_text", "")
        for item in search_body["items"]
    )

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        import json

        assert request.url.path.endswith("/api/chat")
        payload = json.loads(request.content.decode())
        assert payload["stream"] is False
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": expected_answer}},
        )

    monkeypatch.setattr(
        "smarthris.services.hr_document_qa_service.OllamaChatClient",
        lambda s, **kw: OllamaChatClient(
            s,
            transport=httpx.MockTransport(ollama_handler),
            **{k: v for k, v in kw.items() if k != "transport"},
        ),
    )

    rag = pipeline_api_client.post(
        "/rag/query",
        json={"question": "How many compassionate leave days do staff receive?"},
    )
    assert rag.status_code == 200, rag.text
    rag_body = rag.json()
    assert rag_body["answer_status"] == RagAnswerStatus.ANSWERED.value
    assert rag_body["index_ready"] is True
    assert rag_body["retrieval_mode"] == "hybrid"
    assert expected_answer.split("[")[0].strip() in rag_body["answer"]
    assert "storage_path" not in rag_body
    assert rag_body["sources"]
    assert any(source["document_id"] == doc_id for source in rag_body["sources"])
    assert any(
        policy_phrase in source["excerpt"].lower() for source in rag_body["sources"]
    )


def test_rag_query_index_not_ready_skips_ollama(
    db_session: Session,
    integration_database_url: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hr_user = create_test_user(db_session, role=UserRole.HR)
    settings = _pipeline_settings(tmp_path, integration_database_url)
    app = create_app(settings)
    app.state.db_session_factory = _TrackingSessionFactory(db_session)

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, hr_user)

    ollama_cls = MagicMock()
    monkeypatch.setattr(
        "smarthris.services.hr_document_qa_service.OllamaChatClient",
        ollama_cls,
    )

    encode_patch = patch(
        "smarthris.services.hr_document_retrieval_service.encode_texts",
        side_effect=_encode_stub,
    )
    dimension_patch = patch(
        "smarthris.services.hr_document_retrieval_service.embedding_dimension",
        return_value=8,
    )
    with encode_patch, dimension_patch, TestClient(app) as client:
        response = client.post(
            "/rag/query",
            json={"question": "Any HR policy details?"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["answer_status"] == RagAnswerStatus.INDEX_NOT_READY.value
    assert payload["index_ready"] is False
    assert payload["model"] is None
    assert payload["sources"] == []
    ollama_cls.assert_not_called()