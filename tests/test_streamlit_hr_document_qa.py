"""Phase 5.9 — Streamlit HR Document Q&A page tests."""

from __future__ import annotations

from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from smarthris.schemas.rag import RagAnswerStatus, RagQueryResponse
from streamlit_app.api_client import ApiError
from streamlit_app.constants import NAV_HR_DOCUMENT_QA
from streamlit_app.pages.hr_document_qa import (
    SESSION_CHAT_HISTORY,
    SESSION_LAST_ERROR,
    chat_turn_from_response,
    doc_type_for_api,
    normalize_question,
    submit_rag_query,
)

_TIMEOUT = 30


def _rag_response(**overrides: object) -> RagQueryResponse:
    base = {
        "question": "leave?",
        "answer": "Fifteen days [S1].",
        "answer_status": "answered",
        "retrieval_mode": "hybrid",
        "index_ready": True,
        "model": "llama3.2",
        "sources": [
            {
                "rank": 1,
                "chunk_id": 1,
                "document_id": "00000000-0000-4000-8000-000000000001",
                "document_title": "Leave Policy",
                "original_filename": "leave.txt",
                "doc_type": "policy",
                "chunk_index": 0,
                "page_number": 2,
                "section_title": "Annual",
                "excerpt": "Fifteen days annual leave.",
                "score": 0.5,
            }
        ],
    }
    base.update(overrides)
    return RagQueryResponse.model_validate(base)


class RecordingRagClient:
    def __init__(
        self,
        response: RagQueryResponse | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.response = response or _rag_response()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def query_rag(self, question: str, **kwargs: Any) -> RagQueryResponse:
        self.calls.append({"question": question, **kwargs})
        if self.error is not None:
            raise self.error
        return self.response


def test_normalize_question() -> None:
    assert normalize_question("  hello  ") == "hello"
    assert normalize_question("   ") is None
    assert normalize_question("x" * 2001) is None


def test_doc_type_for_api() -> None:
    assert doc_type_for_api("all") is None
    assert doc_type_for_api("policy") == "policy"


def _run_page(api_client: RecordingRagClient | None = None) -> AppTest:
    def _script(client=None) -> None:
        from streamlit_app.auth import enable_page_guard_bypass_for_tests
        from streamlit_app.pages.hr_document_qa import render as render_qa

        enable_page_guard_bypass_for_tests()
        render_qa(api_client=client)

    return AppTest.from_function(
        _script,
        default_timeout=_TIMEOUT,
        kwargs={"client": api_client},
    ).run()


def test_page_renders_title() -> None:
    at = _run_page(RecordingRagClient())
    assert not at.exception
    assert any(NAV_HR_DOCUMENT_QA in t.value for t in at.title)


def test_ask_appends_history_on_success() -> None:
    client = RecordingRagClient()
    at = _run_page(client)
    at.text_area[0].set_value("How much leave?")
    at.button[1].click().run()
    assert client.calls
    assert at.session_state[SESSION_CHAT_HISTORY]
    assert at.session_state[SESSION_CHAT_HISTORY][0]["answer_status"] == "answered"
    assert "Fifteen days" in at.session_state[SESSION_CHAT_HISTORY][0]["answer"]


def test_empty_question_does_not_call_api() -> None:
    client = RecordingRagClient()
    at = _run_page(client)
    at.text_area[0].set_value("   ")
    at.button[1].click().run()
    assert not client.calls
    assert at.session_state.get(SESSION_LAST_ERROR)


def test_source_excerpt_visible_in_ui() -> None:
    client = RecordingRagClient()
    at = _run_page(client)
    at.text_area[0].set_value("leave policy")
    at.button[1].click().run()
    text_blob = " ".join(
        [c.value for c in at.caption] + [m.value for m in at.markdown]
    )
    assert "Fifteen days annual leave" in text_blob


def test_index_not_ready_refusal() -> None:
    client = RecordingRagClient(
        _rag_response(
            answer="I don't have enough information in the indexed HR documents to answer that.",
            answer_status="index_not_ready",
            index_ready=False,
            model=None,
            sources=[],
        )
    )
    at = _run_page(client)
    at.text_area[0].set_value("anything")
    at.button[1].click().run()
    assert at.session_state[SESSION_CHAT_HISTORY][0]["answer_status"] == "index_not_ready"
    assert any("not indexed" in w.value.lower() for w in at.warning)


def test_insufficient_context_refusal() -> None:
    client = RecordingRagClient(
        _rag_response(
            answer="I don't have enough information in the indexed HR documents to answer that.",
            answer_status="insufficient_context",
            sources=[],
            model=None,
        )
    )
    at = _run_page(client)
    at.text_area[0].set_value("obscure topic")
    at.button[1].click().run()
    assert at.session_state[SESSION_CHAT_HISTORY][0]["answer_status"] == "insufficient_context"
    assert any("rephrasing" in i.value.lower() for i in at.info)


def test_api_503_shows_error_without_crash() -> None:
    client = RecordingRagClient(
        error=ApiError("Ollama down", status_code=503, code="ollama_unavailable"),
    )
    at = _run_page(client)
    at.text_area[0].set_value("question")
    at.button[1].click().run()
    assert not at.exception
    assert at.session_state.get(SESSION_LAST_ERROR)
    assert not at.session_state.get(SESSION_CHAT_HISTORY)


def test_failed_request_preserves_prior_history() -> None:
    client = RecordingRagClient()
    at = _run_page(client)
    at.text_area[0].set_value("first")
    at.button[1].click().run()
    assert len(at.session_state[SESSION_CHAT_HISTORY]) == 1

    client.error = ApiError("fail", status_code=503, code="ollama_unavailable")
    at.text_area[0].set_value("second")
    at.button[1].click().run()
    assert len(at.session_state[SESSION_CHAT_HISTORY]) == 1


def test_forwards_retrieval_options() -> None:
    client = RecordingRagClient()
    at = _run_page(client)
    at.selectbox[0].set_value("semantic")
    at.selectbox[1].set_value(3)
    at.selectbox[2].set_value("policy")
    at.text_area[0].set_value("filtered")
    at.button[1].click().run()
    call = client.calls[-1]
    assert call["retrieval_mode"] == "semantic"
    assert call["top_k"] == 3
    assert call["doc_type"] == "policy"


def test_submit_rag_query_success() -> None:
    client = RecordingRagClient()
    response, err = submit_rag_query(
        client,  # type: ignore[arg-type]
        question="q",
        retrieval_mode="hybrid",
        top_k=5,
        doc_type=None,
    )
    assert err is None
    assert response is not None
    assert response.answer_status == RagAnswerStatus.ANSWERED


def test_chat_turn_from_response() -> None:
    turn = chat_turn_from_response(question="q", response=_rag_response())
    assert turn["question"] == "q"
    assert turn["sources"][0]["excerpt"]
    assert "storage_path" not in turn["sources"][0]