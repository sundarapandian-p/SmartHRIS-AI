"""Data access for bulk import jobs."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from smarthris.models.import_job import (
    ImportFileType,
    ImportJob,
    ImportJobStatus,
    ImportTarget,
)

_ACTIVE_RETRY_STATUSES = (ImportJobStatus.PENDING, ImportJobStatus.RUNNING)


class ImportJobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        filename: str,
        file_type: ImportFileType,
        dry_run: bool = False,
        job_id: str | None = None,
        parent_job_id: str | None = None,
        import_target: ImportTarget = ImportTarget.EMPLOYEES,
    ) -> ImportJob:
        job = ImportJob(
            id=job_id or str(uuid4()),
            filename=filename,
            file_type=file_type,
            dry_run=dry_run,
            status=ImportJobStatus.PENDING,
            import_target=import_target,
            parent_job_id=parent_job_id,
        )
        self._session.add(job)
        self._session.flush()
        return job

    def get_by_id(self, job_id: str) -> ImportJob | None:
        return self._session.get(ImportJob, job_id)

    def get_by_id_for_update(self, job_id: str) -> ImportJob | None:
        """Load a job row with SELECT ... FOR UPDATE (caller owns the transaction)."""
        query = select(ImportJob).where(ImportJob.id == job_id).with_for_update()
        return self._session.scalars(query).one_or_none()

    def find_active_child_id_for_update(self, parent_job_id: str) -> str | None:
        """
        Return an id of a pending/running child, locking matching rows.

        Used inside the same transaction as parent FOR UPDATE + child create.
        """
        query = (
            select(ImportJob.id)
            .where(
                ImportJob.parent_job_id == parent_job_id,
                ImportJob.status.in_(_ACTIVE_RETRY_STATUSES),
            )
            .with_for_update()
            .limit(1)
        )
        return self._session.scalars(query).first()

    def mark_running(self, job: ImportJob) -> ImportJob:
        job.status = ImportJobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        self._session.flush()
        return job

    def mark_completed(self, job: ImportJob) -> ImportJob:
        job.status = ImportJobStatus.COMPLETED
        job.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return job

    def mark_failed(self, job: ImportJob, *, error_message: str) -> ImportJob:
        job.status = ImportJobStatus.FAILED
        job.error_message = error_message
        job.finished_at = datetime.now(timezone.utc)
        self._session.flush()
        return job

    def update_counts(
        self,
        job: ImportJob,
        *,
        total_rows: int | None = None,
        processed_rows: int | None = None,
        success_count: int | None = None,
        failed_count: int | None = None,
    ) -> ImportJob:
        if total_rows is not None:
            job.total_rows = total_rows
        if processed_rows is not None:
            job.processed_rows = processed_rows
        if success_count is not None:
            job.success_count = success_count
        if failed_count is not None:
            job.failed_count = failed_count
        self._session.flush()
        return job

    def list_recent(self, *, limit: int = 20) -> list[ImportJob]:
        query = select(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit)
        return list(self._session.scalars(query).all())
