"""Create pending import jobs from CSV uploads and query import errors."""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import NotFoundError, PayloadTooLargeError, StorageError, ValidationError
from smarthris.importing.storage import (
    assert_csv_filename,
    delete_import_file,
    ensure_import_temp_dir,
    import_storage_path,
    new_job_id,
    sanitize_original_filename,
    save_upload_stream,
)
from smarthris.models.import_job import ImportFileType, ImportJob
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.schemas.import_job import ImportErrorListResponse, ImportErrorRead, ImportJobRead


class ImportUploadService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._jobs = ImportJobRepository(session)
        self._errors = ImportErrorRepository(session)

    def create_from_upload(
        self,
        *,
        upload: UploadFile,
        dry_run: bool = False,
    ) -> ImportJobRead:
        display_name = sanitize_original_filename(upload.filename)
        assert_csv_filename(display_name)

        job_id = new_job_id()
        ensure_import_temp_dir(self._settings.import_temp_dir)
        destination = import_storage_path(self._settings.import_temp_dir, job_id)

        try:
            save_upload_stream(
                source=upload.file,
                destination=destination,
                max_upload_bytes=self._settings.max_upload_bytes,
            )
        except (ValidationError, StorageError, PayloadTooLargeError):
            raise
        except Exception as exc:
            delete_import_file(destination)
            raise StorageError("Failed to store uploaded file") from exc

        try:
            job = self._jobs.create(
                filename=display_name,
                file_type=ImportFileType.CSV,
                dry_run=dry_run,
                job_id=job_id,
            )
            self._session.commit()
            self._session.refresh(job)
            return ImportJobRead.model_validate(job)
        except Exception:
            self._session.rollback()
            delete_import_file(destination)
            raise

    def list_errors(
        self,
        job_id: str,
        *,
        page: int,
        page_size: int,
    ) -> ImportErrorListResponse:
        job = self._jobs.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Import job not found")

        total = self._errors.count_for_job(job_id)
        offset = (page - 1) * page_size
        rows = self._errors.list_for_job(job_id, offset=offset, limit=page_size)
        pages = math.ceil(total / page_size) if total else 0
        return ImportErrorListResponse(
            import_job_id=job_id,
            items=[ImportErrorRead.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    def stored_path_for_job(self, job: ImportJob) -> Path:
        """Helper for later phases; not exposed via API."""
        return import_storage_path(self._settings.import_temp_dir, job.id)
