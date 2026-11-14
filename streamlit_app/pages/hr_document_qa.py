"""HR Document Q&A — chat UI via POST /rag/query."""

from __future__ import annotations

from typing import Any, TypedDict

import streamlit as st

from smarthris.schemas.rag import RagAnswerStatus, RagQueryResponse
from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import NAV_HR_DOCUMENT_QA

MAX_QUESTION_LENGTH = 2000
DEFAULT_TOP_K = 5

DOC_TYPE_ALL = "all"
DOC_TYPE_OPTIONS = (DOC_TYPE_ALL, "policy", "handbook", "other")
RETRIEVAL_MODE_OPTIONS = ("hybrid", "semantic", "keyword")

SESSION_CHAT_HISTORY = "rag_chat_history"
SESSION_LAST_ERROR = "rag_last_error"
SESSION_RETRIEVAL_MODE = "rag_retrieval_mode"
SESSION_TOP_K = "rag_top_k"
SESSION_DOC_TYPE = "rag_doc_type_filter"
SESSION_QUESTION_DRAFT = "rag_question_draft"

OLLAMA_503_HINT = (
    "The language model service (Ollama) is unavailable. Start Ollama, pull the "
    "configured model, and confirm OLLAMA_BASE_URL / OLLAMA_MODEL on the API."
)


class ChatTurn(TypedDict):
    question: str
    answer: str
    answer_status: str
    retrieval_mode: str
    sources: list[dict[str, Any]]
    model: str | None


def normalize_question(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > MAX_QUESTION_LENGTH:
        return None
    return text


def doc_type_for_api(value: str | None) -> str | None:
    if value is None or value == DOC_TYPE_ALL:
        return None
    if value in {"policy", "handbook", "other"}:
        return value
    return None


def chat_turn_from_response(
    *,
    question: str,
    response: RagQueryResponse,
) -> ChatTurn:
    return {
        "question": question,
        "answer": response.answer,
        "answer_status": response.answer_status.value,
        "retrieval_mode": response.retrieval_mode.value,
        "sources": [source.model_dump(mode="json") for source in response.sources],
        "model": response.model,
    }


def format_api_error(exc: ApiError) -> str:
    if exc.status_code == 503:
        base = str(exc)
        if exc.code and exc.code.startswith("ollama"):
            return f"{base} {OLLAMA_503_HINT}"
        return base
    if exc.status_code == 400:
        return f"Request error: {exc}"
    if exc.status_code == 422:
        return f"Invalid options: {exc}"
    return str(exc)


def submit_rag_query(
    client: SmartHrisApiClient,
    *,
    question: str,
    retrieval_mode: str,
    top_k: int,
    doc_type: str | None,
) -> tuple[RagQueryResponse | None, str | None]:
    """Call the API; return (response, error_message)."""
    try:
        response = client.query_rag(
            question,
            retrieval_mode=retrieval_mode,
            top_k=top_k,
            doc_type=doc_type,
        )
    except ApiError as exc:
        return None, format_api_error(exc)
    except Exception as exc:
        return None, f"Unexpected error: {exc}"
    return response, None


def render_sources(sources: list[dict[str, Any]], *, key_suffix: str) -> None:
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})", expanded=False):
        for source in sources:
            rank = source.get("rank")
            title = source.get("document_title", "")
            filename = source.get("original_filename", "")
            doc_type = source.get("doc_type", "")
            page = source.get("page_number")
            section = source.get("section_title")
            score = source.get("score")
            excerpt = source.get("excerpt", "")
            st.markdown(f"**[{rank}] {title}** (`{filename}`, {doc_type})")
            meta_parts = []
            if page is not None:
                meta_parts.append(f"page {page}")
            if section:
                meta_parts.append(f"§ {section}")
            if score is not None:
                meta_parts.append(f"score {score:.4f}" if isinstance(score, float) else f"score {score}")
            if meta_parts:
                st.caption(" · ".join(meta_parts))
            st.caption(excerpt)


def render_turn(turn: ChatTurn, *, index: int) -> None:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        status = turn["answer_status"]
        if status == RagAnswerStatus.ANSWERED.value:
            st.markdown(turn["answer"])
            if turn.get("model"):
                st.caption(f"Model: {turn['model']}")
        elif status == RagAnswerStatus.INDEX_NOT_READY.value:
            st.warning(
                "HR documents are not indexed yet (search indexes are not ready). "
                "Upload and index documents via the API before using Q&A."
            )
            st.markdown(turn["answer"])
        elif status == RagAnswerStatus.INSUFFICIENT_CONTEXT.value:
            st.info(
                "No sufficiently relevant indexed chunks were retrieved. "
                "Try rephrasing your question or adjust the document type filter."
            )
            st.markdown(turn["answer"])
        else:
            st.markdown(turn["answer"])
        render_sources(turn.get("sources") or [], key_suffix=f"hist-{index}")


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    from streamlit_app.auth import get_api_client, require_page_access

    require_page_access("hr_document_qa")
    st.title(NAV_HR_DOCUMENT_QA)
    st.caption(
        "Ask questions based on indexed HR documents. Answers use the FastAPI "
        "``POST /rag/query`` endpoint (retrieval + Ollama). Upload and indexing "
        "remain API-only in this phase."
    )

    client = api_client or get_api_client()

    if SESSION_CHAT_HISTORY not in st.session_state:
        st.session_state[SESSION_CHAT_HISTORY] = []
    if SESSION_RETRIEVAL_MODE not in st.session_state:
        st.session_state[SESSION_RETRIEVAL_MODE] = "hybrid"
    if SESSION_TOP_K not in st.session_state:
        st.session_state[SESSION_TOP_K] = DEFAULT_TOP_K
    if SESSION_DOC_TYPE not in st.session_state:
        st.session_state[SESSION_DOC_TYPE] = DOC_TYPE_ALL
    if SESSION_QUESTION_DRAFT not in st.session_state:
        st.session_state[SESSION_QUESTION_DRAFT] = ""

    with st.expander("Search options", expanded=True):
        opt1, opt2, opt3 = st.columns(3)
        with opt1:
            mode_index = RETRIEVAL_MODE_OPTIONS.index(
                st.session_state[SESSION_RETRIEVAL_MODE]
            ) if st.session_state[SESSION_RETRIEVAL_MODE] in RETRIEVAL_MODE_OPTIONS else 0
            retrieval_mode = st.selectbox(
                "Retrieval mode",
                options=RETRIEVAL_MODE_OPTIONS,
                index=mode_index,
            )
        with opt2:
            top_k = st.selectbox(
                "Top K",
                options=list(range(1, 21)),
                index=max(0, min(19, int(st.session_state[SESSION_TOP_K]) - 1)),
            )
        with opt3:
            doc_index = (
                DOC_TYPE_OPTIONS.index(st.session_state[SESSION_DOC_TYPE])
                if st.session_state[SESSION_DOC_TYPE] in DOC_TYPE_OPTIONS
                else 0
            )
            doc_type_filter = st.selectbox(
                "Document type",
                options=DOC_TYPE_OPTIONS,
                format_func=lambda v: v.title() if v != DOC_TYPE_ALL else "All",
                index=doc_index,
            )
        st.session_state[SESSION_RETRIEVAL_MODE] = retrieval_mode
        st.session_state[SESSION_TOP_K] = int(top_k)
        st.session_state[SESSION_DOC_TYPE] = doc_type_filter

    history: list[ChatTurn] = list(st.session_state[SESSION_CHAT_HISTORY])
    for index, turn in enumerate(history):
        render_turn(turn, index=index)

    if st.session_state.get(SESSION_LAST_ERROR):
        st.error(st.session_state[SESSION_LAST_ERROR])

    col_clear, _ = st.columns([1, 3])
    with col_clear:
        if st.button("Clear history", disabled=not history):
            st.session_state[SESSION_CHAT_HISTORY] = []
            st.session_state[SESSION_LAST_ERROR] = None
            st.rerun()

    question_input = st.text_area(
        "Your question",
        value=st.session_state[SESSION_QUESTION_DRAFT],
        max_chars=MAX_QUESTION_LENGTH,
        height=100,
        placeholder="e.g. How many days of annual leave do new hires receive?",
    )
    ask_clicked = st.button("Ask", type="primary")

    if ask_clicked:
        normalized = normalize_question(question_input)
        if normalized is None:
            if question_input and question_input.strip() and len(question_input.strip()) > MAX_QUESTION_LENGTH:
                st.session_state[SESSION_LAST_ERROR] = (
                    f"Question must be at most {MAX_QUESTION_LENGTH} characters."
                )
            else:
                st.session_state[SESSION_LAST_ERROR] = "Enter a question before asking."
            st.rerun()

        st.session_state[SESSION_QUESTION_DRAFT] = ""
        response, error = submit_rag_query(
            client,
            question=normalized,
            retrieval_mode=retrieval_mode,
            top_k=int(top_k),
            doc_type=doc_type_for_api(doc_type_filter),
        )
        if error is not None:
            st.session_state[SESSION_LAST_ERROR] = error
            st.rerun()

        st.session_state[SESSION_LAST_ERROR] = None
        turn = chat_turn_from_response(question=normalized, response=response)
        st.session_state[SESSION_CHAT_HISTORY] = history + [turn]
        st.rerun()


if __name__ == "__main__":
    render()