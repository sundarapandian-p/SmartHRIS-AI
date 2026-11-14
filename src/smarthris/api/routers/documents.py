"""HR document upload and metadata routes."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.models.hr_document import HrDocumentStatus, HrDocumentType
from smarthris.schemas.hr_document import (
    HrDocumentListResponse,
    HrDocumentRead,
    HrDocumentSearchRequest,
    HrDocumentSearchResponse,
)
from smarthris.services.hr_document_processing_service import (
    process_hr_document,
    request_document_reindex,
)
from smarthris.services.hr_document_retrieval_service import HrDocumentRetrievalService
from smarthris.services.hr_document_service import HrDocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_hr_document_service(
    request: Request,
    db: Session = Depends(get_db),
) -> HrDocumentService:
    return HrDocumentService(db, get_settings_from_app(request))


def _schedule_document_processing(
    *,
    background_tasks: BackgroundTasks,
    request: Request,
    document_id: str,
) -> None:
    background_tasks.add_task(
        process_hr_document,
        document_id,
        session_factory=request.app.state.db_session_factory,
        settings=request.app.state.settings,
    )


@router.post("", response_model=HrDocumentRead, status_code=201)
def upload_document(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    doc_type: HrDocumentType | None = Form(None),
    service: HrDocumentService = Depends(get_hr_document_service),
) -> HrDocumentRead:
    """
    Accept a PDF/DOCX/TXT upload, store it privately, and create pending metadata.

    Schedules background extraction, chunking, and indexing. Does not return
    file contents or storage paths.
    """
    result = service.create_from_upload(upload=file, title=title, doc_type=doc_type)
    _schedule_document_processing(
        background_tasks=background_tasks,
        request=request,
        document_id=result.id,
    )
    return result


@router.get("", response_model=HrDocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_type: HrDocumentType | None = None,
    status: HrDocumentStatus | None = None,
    service: HrDocumentService = Depends(get_hr_document_service),
) -> HrDocumentListResponse:
    return service.list(
        page=page,
        page_size=page_size,
        doc_type=doc_type,
        status=status,
    )


@router.post("/search", response_model=HrDocumentSearchResponse)
def search_documents(
    payload: HrDocumentSearchRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> HrDocumentSearchResponse:
    """
    Semantic search over indexed document chunks (FAISS + MySQL).

    Malformed JSON or invalid fields may return HTTP 422; whitespace-only ``query``
    returns HTTP 400. Requires a single Uvicorn worker (see README). Results may
    briefly use the prior in-memory index while reindexing completes.
    """
    settings = get_settings_from_app(request)
    return HrDocumentRetrievalService(db, settings).search(payload)


@router.get("/{document_id}", response_model=HrDocumentRead)
def get_document(
    document_id: str,
    service: HrDocumentService = Depends(get_hr_document_service),
) -> HrDocumentRead:
    return service.get(document_id)


@router.post("/{document_id}/reindex", response_model=HrDocumentRead)
def reindex_document(
    document_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
    service: HrDocumentService = Depends(get_hr_document_service),
) -> HrDocumentRead:
    """Re-run extraction and indexing for failed or previously indexed documents."""
    request_document_reindex(db, document_id)
    _schedule_document_processing(
        background_tasks=background_tasks,
        request=request,
        document_id=document_id,
    )
    return service.get(document_id)
