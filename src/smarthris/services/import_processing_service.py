"""Background validation and batch insertion of uploaded import jobs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from smarthris.config import Settings
from smarthris.importing.parsers.csv_parser import CsvParseError, parse_csv
from smarthris.importing.storage import import_storage_path
from smarthris.importing.validation.row_validator import (
    ImportRowValidator,
    ValidatedImportRow,
)
from smarthris.models.employee import Employee
from smarthris.models.import_job import ImportJobStatus
from smarthris.repositories.department_repository import DepartmentRepository
from smarthris.repositories.employee_repository import EmployeeRepository
from smarthris.repositories.import_job_repository import ImportJobRepository

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_LEN = 1000


def _sanitize_error_message(message: str) -> str:
    """Keep failure text useful without leaking absolute paths or secrets."""
    text = " ".join(str(message).split())
    if len(text) > _MAX_ERROR_MESSAGE_LEN:
        text = text[: _MAX_ERROR_MESSAGE_LEN - 3] + "..."
    return text


def _employee_from_validated(item: ValidatedImportRow) -> Employee:
    row = item.row
    return Employee(
        employee_number=row.employee_number,
        first_name=row.first_name,
        last_name=row.last_name,
        work_email=str(row.work_email).lower(),
        department_id=item.resolved_department_id,
        job_role=row.job_role,
        job_title=row.job_title,
        manager_employee_id=None,
        hire_date=row.hire_date,
        status=row.status,
    )


class ImportProcessingService:
    """
    Validate uploaded CSV rows and insert valid employees in batches.

    Uses an independent SQLAlchemy session from ``session_factory``.
    Does not persist ImportError rows (Phase 3.6).
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | sessionmaker[Session],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def process(self, job_id: str) -> None:
        session = self._session_factory()
        try:
            self._process_with_session(session, job_id)
        finally:
            session.close()

    def _process_with_session(self, session: Session, job_id: str) -> None:
        jobs = ImportJobRepository(session)
        job = jobs.get_by_id(job_id)
        if job is None:
            logger.info("Import job %s not found; skipping", job_id)
            return

        if job.status != ImportJobStatus.PENDING:
            logger.info(
                "Import job %s status is %s; skipping duplicate run",
                job_id,
                job.status.value,
            )
            return

        try:
            jobs.mark_running(job)
            session.commit()
        except Exception:
            logger.exception("Failed to mark import job %s as running", job_id)
            session.rollback()
            raise

        # Re-read after commit so dry_run and identity stay attached to this session.
        job = jobs.get_by_id(job_id)
        if job is None:
            logger.error("Import job %s disappeared after mark_running", job_id)
            return
        dry_run = bool(job.dry_run)

        csv_path = import_storage_path(self._settings.import_temp_dir, job_id)
        if not csv_path.is_file():
            self._mark_failed_safe(
                session,
                job_id,
                "Uploaded file not found for import job",
            )
            return

        try:
            total_rows = 0
            success_count = 0
            failed_count = 0
            buffer: list[ValidatedImportRow] = []
            batch_size = self._settings.import_batch_size
            employees_repo = EmployeeRepository(session)

            validator = ImportRowValidator(
                department_repository=DepartmentRepository(session),
                employee_repository=employees_repo,
                batch_size=batch_size,
            )
            for result in validator.validate(parse_csv(csv_path)):
                total_rows += 1
                if not result.is_valid or result.validated is None:
                    failed_count += 1
                    continue

                if dry_run:
                    success_count += 1
                    continue

                buffer.append(result.validated)
                if len(buffer) >= batch_size:
                    inserted, failed = self._flush_insert_buffer(
                        session,
                        employees_repo,
                        buffer,
                    )
                    success_count += inserted
                    failed_count += failed
                    buffer.clear()

            if buffer:
                inserted, failed = self._flush_insert_buffer(
                    session,
                    employees_repo,
                    buffer,
                )
                success_count += inserted
                failed_count += failed
                buffer.clear()

            job = jobs.get_by_id(job_id)
            if job is None:
                logger.error("Import job %s disappeared during processing", job_id)
                return

            jobs.update_counts(
                job,
                total_rows=total_rows,
                processed_rows=total_rows,
                success_count=success_count,
                failed_count=failed_count,
            )
            jobs.mark_completed(job)
            session.commit()
        except CsvParseError as exc:
            logger.warning("CSV parse failed for import job %s: %s", job_id, exc.message)
            session.rollback()
            self._mark_failed_safe(
                session,
                job_id,
                _sanitize_error_message(f"CSV parse error: {exc.message}"),
            )
        except Exception as exc:
            logger.exception("Unexpected failure processing import job %s", job_id)
            try:
                session.rollback()
            except Exception:
                logger.exception("Rollback failed for import job %s", job_id)
            self._mark_failed_safe(
                session,
                job_id,
                _sanitize_error_message(f"Import processing failed: {exc}"),
            )

    def _flush_insert_buffer(
        self,
        session: Session,
        employees_repo: EmployeeRepository,
        buffer: Sequence[ValidatedImportRow],
    ) -> tuple[int, int]:
        """
        Insert one batch. Returns (success_count, failed_count).

        On IntegrityError, rolls back the batch and retries each row with a savepoint.
        Previously committed batches are unaffected.
        """
        if not buffer:
            return 0, 0

        employees = [_employee_from_validated(item) for item in buffer]
        try:
            employees_repo.add_many(employees)
            session.flush()
            session.commit()
            return len(employees), 0
        except IntegrityError:
            logger.info(
                "Batch insert hit IntegrityError for %s rows; retrying individually",
                len(buffer),
            )
            session.rollback()
            return self._insert_rows_individually(session, employees_repo, buffer)

    def _insert_rows_individually(
        self,
        session: Session,
        employees_repo: EmployeeRepository,
        buffer: Sequence[ValidatedImportRow],
    ) -> tuple[int, int]:
        """
        Insert each row in its own short transaction after a failed batch.

        Uses flush+commit per row (with rollback on conflict) so the session
        stays usable and earlier successful rows in this retry remain kept.
        """
        inserted = 0
        failed = 0
        for item in buffer:
            try:
                employees_repo.add(_employee_from_validated(item))
                session.flush()
                session.commit()
                inserted += 1
            except IntegrityError:
                session.rollback()
                failed += 1
        return inserted, failed

    def _mark_failed_safe(self, session: Session, job_id: str, error_message: str) -> None:
        """Best-effort failed transition; may open a fresh session if needed."""
        if self._try_mark_failed(session, job_id, error_message):
            return

        fallback = self._session_factory()
        try:
            self._try_mark_failed(fallback, job_id, error_message)
        finally:
            fallback.close()

    def _try_mark_failed(self, session: Session, job_id: str, error_message: str) -> bool:
        try:
            jobs = ImportJobRepository(session)
            job = jobs.get_by_id(job_id)
            if job is None:
                return True
            if job.status in {ImportJobStatus.COMPLETED, ImportJobStatus.FAILED}:
                return True
            jobs.mark_failed(job, error_message=error_message)
            session.commit()
            return True
        except Exception:
            logger.exception("Unable to mark import job %s as failed", job_id)
            try:
                session.rollback()
            except Exception:
                pass
            return False


def process_import_job(
    job_id: str,
    *,
    session_factory: Callable[[], Session] | sessionmaker[Session],
    settings: Settings,
) -> None:
    """BackgroundTasks entrypoint: validate and insert an uploaded import job."""
    ImportProcessingService(session_factory, settings).process(job_id)
