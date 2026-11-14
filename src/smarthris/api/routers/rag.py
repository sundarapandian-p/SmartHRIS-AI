"""RAG question-answering routes."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.schemas.rag import RagQueryRequest, RagQueryResponse
from smarthris.services.hr_document_qa_service import HrDocumentQaService

router = APIRouter(prefix="/rag", tags=["rag"])


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


@router.post("/query", response_model=RagQueryResponse)
def rag_query(
    payload: RagQueryRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> RagQueryResponse:
    """
    Answer a question using hybrid (default) or single-channel retrieval + Ollama.

    Returns refusal responses without calling Ollama when indexes are missing or
    retrieval returns no chunks. Ollama failures surface as HTTP 503 with safe codes.
    """
    settings = get_settings_from_app(request)
    return HrDocumentQaService(db, settings).query(payload)