"""Schemas for HR document RAG question answering."""

import enum

from pydantic import BaseModel, Field

from smarthris.models.hr_document import HrDocumentType
from smarthris.schemas.hr_document import HrDocumentRetrievalMode


class RagAnswerStatus(str, enum.Enum):
    """Outcome of a RAG query without exposing upstream failure details."""

    ANSWERED = "answered"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    INDEX_NOT_READY = "index_not_ready"


class RagQueryRequest(BaseModel):
    """Question over indexed HR documents with optional retrieval tuning."""

    question: str = Field(min_length=1, max_length=2000)
    retrieval_mode: HrDocumentRetrievalMode = HrDocumentRetrievalMode.HYBRID
    top_k: int | None = Field(default=None, ge=1, le=20)
    doc_type: HrDocumentType | None = None


class RagQuerySourceItem(BaseModel):
    """One retrieved chunk used as grounding context (no storage paths)."""

    rank: int = Field(ge=1)
    chunk_id: int = Field(ge=1)
    document_id: str = Field(min_length=36, max_length=36)
    document_title: str = Field(min_length=1, max_length=255)
    original_filename: str = Field(min_length=1, max_length=255)
    doc_type: HrDocumentType
    chunk_index: int = Field(ge=0)
    page_number: int | None = Field(default=None, ge=1)
    section_title: str | None = Field(default=None, max_length=255)
    excerpt: str = Field(min_length=1)
    score: float


class RagQueryResponse(BaseModel):
    """Answer with structured sources from retrieval (not parsed from model text)."""

    question: str = Field(min_length=1, max_length=2000)
    answer: str
    answer_status: RagAnswerStatus
    retrieval_mode: HrDocumentRetrievalMode
    index_ready: bool
    sources: list[RagQuerySourceItem]
    model: str | None = Field(
        default=None,
        description="Ollama model tag when generation ran; omitted when Ollama was not called.",
    )