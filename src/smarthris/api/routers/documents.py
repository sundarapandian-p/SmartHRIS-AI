"""HR document upload and metadata routes."""

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.models.hr_document import HrDocumentStatus, HrDocumentType
from smarthris.schemas.hr_document import HrDocumentListResponse, HrDocumentRead
from smarthris.services.hr_document_service import HrDocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_hr_document_service(
    request: Request,
    db: Session = Depends(get_db),
) -> HrDocumentService:
    return HrDocumentService(db, get_settings_from_app(request))


@router.post("", response_model=HrDocumentRead, status_code=201)
def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    doc_type: HrDocumentType | None = Form(None),
    service: HrDocumentService = Depends(get_hr_document_service),
) -> HrDocumentRead:
    """
    Accept a PDF/DOCX/TXT upload, store it privately, and create pending metadata.

    Does not extract text, index, or return file contents or storage paths.
    """
    return service.create_from_upload(upload=file, title=title, doc_type=doc_type)


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


@router.get("/{document_id}", response_model=HrDocumentRead)
def get_document(
    document_id: str,
    service: HrDocumentService = Depends(get_hr_document_service),
) -> HrDocumentRead:
    return service.get(document_id)
