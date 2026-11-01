"""Create pending import jobs from CSV uploads, retries, and query import errors."""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    StorageError,
    ValidationError,
)
from smarthris.importing.storage import (
    assert_csv_filename,
    copy_import_file,
    delete_import_file,
    ensure_import_temp_dir,
    import_storage_path,
    new_job_id,
    sanitize_original_filename,
    save_upload_stream,
)
from smarthris.models.import_job import ImportFileType, ImportJob, ImportJobStatus
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.schemas.import_job import ImportErrorListResponse, ImportErrorRead, ImportJobRead


def _retry_eligibility_conflict(job: ImportJob) -> str | None:
    """Return a conflict message if ``job`` cannot be retried; otherwise None."""
    if job.dry_run:
        return "Dry-run jobs cannot be retried"
    if job.status in {ImportJobStatus.PENDING, ImportJobStatus.RUNNING}:
        return "Import job is still in progress and cannot be retried"
    if job.status == ImportJobStatus.COMPLETED and job.failed_count == 0:
        return "Successful import jobs cannot be retried"
    if job.status == ImportJobStatus.COMPLETED and job.failed_count > 0:
        return None
    if job.status == ImportJobStatus.FAILED:
        return None
    return "Import job cannot be retried"


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

    def create_retry(self, job_id: str) -> ImportJobRead:
        """
        Create a child ImportJob that reprocesses the parent's CSV.

        Concurrency: end the initial read transaction before copy/lock, then
        SELECT parent FOR UPDATE + active-child FOR UPDATE in one fresh txn.
        """
        parent = self._jobs.get_by_id(job_id)
        if parent is None:
            raise NotFoundError("Import job not found")

        parent_id = parent.id
        filename = parent.filename
        file_type = parent.file_type
        source_path = import_storage_path(self._settings.import_temp_dir, parent_id)

        early_conflict = _retry_eligibility_conflict(parent)
        if early_conflict is not None:
            # End the read snapshot before raising so callers keep a clean session.
            self._session.rollback()
            raise ConflictError(early_conflict)

        if not source_path.is_file():
            self._session.rollback()
            raise ConflictError("Import source file is missing and cannot be retried")

        # Mandatory: drop the REPEATABLE READ snapshot from the existence read.
        self._session.rollback()

        child_id = new_job_id()
        ensure_import_temp_dir(self._settings.import_temp_dir)
        destination = import_storage_path(self._settings.import_temp_dir, child_id)

        try:
            copy_import_file(source_path, destination)
        except StorageError:
            delete_import_file(destination)
            raise

        try:
            locked_parent = self._jobs.get_by_id_for_update(parent_id)
            if locked_parent is None:
                raise NotFoundError("Import job not found")

            conflict = _retry_eligibility_conflict(locked_parent)
            if conflict is not None:
                raise ConflictError(conflict)

            if not source_path.is_file():
                raise ConflictError("Import source file is missing and cannot be retried")

            active_child_id = self._jobs.find_active_child_id_for_update(parent_id)
            if active_child_id is not None:
                raise ConflictError("Import job has an active retry")

            child = self._jobs.create(
                filename=filename,
                file_type=file_type,
                dry_run=False,
                job_id=child_id,
                parent_job_id=parent_id,
            )
            self._session.commit()
            self._session.refresh(child)
            return ImportJobRead.model_validate(child)
        except Exception:
            self._session.rollback()
            delete_import_file(destination)
            raise

    def get_job(self, job_id: str) -> ImportJobRead:
        """Return current ImportJob details without modifying the job."""
        job = self._jobs.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        return ImportJobRead.model_validate(job)

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
