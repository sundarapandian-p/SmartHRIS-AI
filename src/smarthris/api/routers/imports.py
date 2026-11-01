"""Bulk employee import upload routes."""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.schemas.import_job import ImportJobRead
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
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    service: ImportUploadService = Depends(get_import_upload_service),
) -> ImportJobRead:
    """
    Accept a CSV upload, store it privately, and create a pending ImportJob.

    Does not parse rows, validate employees, or start background work.
    """
    return service.create_from_upload(upload=file, dry_run=dry_run)
