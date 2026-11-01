"""Bulk employee import upload and error query routes."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.schemas.import_job import ImportErrorListResponse, ImportJobRead
from smarthris.services.import_processing_service import process_import_job
from smarthris.services.import_upload_service import ImportUploadService

router = APIRouter(prefix="/imports", tags=["imports"])


def get_settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_import_upload_service(
    request: Request,
    db: Session = Depends(get_db),
) -> ImportUploadService:
    return ImportUploadService(db, get_settings_from_app(request))


@router.post("", response_model=ImportJobRead, status_code=201)
def upload_import(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """
    Accept a CSV upload, store it privately, create a pending ImportJob,
    then schedule background validation and insertion (respects dry_run).
    """
    result = service.create_from_upload(upload=file, dry_run=dry_run)
    background_tasks.add_task(
        process_import_job,
        result.id,
        session_factory=request.app.state.db_session_factory,
        settings=request.app.state.settings,
    )
    return result


@router.get("/{job_id}", response_model=ImportJobRead)
def get_import_job(
    job_id: str,
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """Return current import job status and counters (read-only)."""
    return service.get_job(job_id)


@router.get("/{job_id}/errors", response_model=ImportErrorListResponse)
def list_import_errors(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportErrorListResponse:
    """Return paginated ImportError rows for one import job (row_number, id order)."""
    return service.list_errors(job_id, page=page, page_size=page_size)
