"""Bulk import upload and error query routes (employees and courses)."""

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


def _schedule_import(
    *,
    background_tasks: BackgroundTasks,
    request: Request,
    job_id: str,
) -> None:
    background_tasks.add_task(
        process_import_job,
        job_id,
        session_factory=request.app.state.db_session_factory,
        settings=request.app.state.settings,
    )


@router.post("", response_model=ImportJobRead, status_code=201)
def upload_import(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """
    Accept an employee CSV upload, store it privately, create a pending ImportJob,
    then schedule background validation and insertion (respects dry_run).
    """
    result = service.create_from_upload(upload=file, dry_run=dry_run)
    _schedule_import(
        background_tasks=background_tasks,
        request=request,
        job_id=result.id,
    )
    return result


@router.post("/courses", response_model=ImportJobRead, status_code=201)
def upload_course_import(
    background_tasks: BackgroundTasks,
    request: Request,
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """
    Accept a course CSV upload, create a COURSES-targeted ImportJob,
    then schedule background course validation and insertion.
    """
    result = service.create_course_from_upload(upload=file, dry_run=dry_run)
    _schedule_import(
        background_tasks=background_tasks,
        request=request,
        job_id=result.id,
    )
    return result


@router.get("/{job_id}", response_model=ImportJobRead)
def get_import_job(
    job_id: str,
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """Return current import job status and counters (read-only)."""
    return service.get_job(job_id)


@router.post("/{job_id}/retry", response_model=ImportJobRead, status_code=201)
def retry_import(
    job_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """
    Create a child ImportJob that reprocesses the parent's stored CSV.

    Schedules background processing only after the child row is committed.
    """
    result = service.create_retry(job_id)
    _schedule_import(
        background_tasks=background_tasks,
        request=request,
        job_id=result.id,
    )
    return result


@router.get("/{job_id}/errors", response_model=ImportErrorListResponse)
def list_import_errors(
    job_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportErrorListResponse:
    """Return paginated ImportError rows for one import job (row_number, id order)."""
    return service.list_errors(job_id, page=page, page_size=page_size)
