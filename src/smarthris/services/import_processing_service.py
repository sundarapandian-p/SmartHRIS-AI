"""Background validation and batch insertion of uploaded import jobs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from smarthris.config import Settings
from smarthris.importing.parsers.csv_parser import CsvParseError, parse_csv
from smarthris.importing.storage import import_storage_path
from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.importing.validation.row_validator import (
    ImportRowValidator,
    ValidatedImportRow,
)
from smarthris.models.employee import Employee
from smarthris.models.import_error import ImportError
from smarthris.models.import_job import ImportJobStatus, ImportTarget
from smarthris.repositories.department_repository import DepartmentRepository
from smarthris.repositories.employee_repository import EmployeeRepository
from smarthris.repositories.import_error_repository import ImportErrorRepository
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


def _import_error_from_issue(job_id: str, issue: ImportRowIssue) -> ImportError:
    return ImportError(
        import_job_id=job_id,
        row_number=issue.row_number,
        employee_number=issue.employee_number,
        record_key=issue.record_key,
        field=issue.field,
        code=issue.code,
        message=issue.message,
    )


def _import_error_from_insert_failure(job_id: str, item: ValidatedImportRow) -> ImportError:
    return ImportError(
        import_job_id=job_id,
        row_number=item.row_number,
        employee_number=item.row.employee_number,
        field=None,
        code=ImportErrorCode.ALREADY_EXISTS.value,
        message=(
            f"Employee '{item.row.employee_number}' could not be inserted "
            "due to a unique constraint conflict"
        ),
    )


class ImportProcessingService:
    """
    Validate uploaded CSV rows, persist ImportError rows in bounded batches,
    and insert valid employees in separate transactions.
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
            error_buffer: list[ImportError] = []
            batch_size = self._settings.import_batch_size
            employees_repo = EmployeeRepository(session)
            errors_repo = ImportErrorRepository(session)

            validator = ImportRowValidator(
                department_repository=DepartmentRepository(session),
                employee_repository=employees_repo,
                batch_size=batch_size,
            )
            for result in validator.validate(parse_csv(csv_path)):
                total_rows += 1
                if not result.is_valid or result.validated is None:
                    failed_count += 1
                    for issue in result.issues:
                        self._queue_error(
                            session,
                            errors_repo,
                            error_buffer,
                            _import_error_from_issue(job_id, issue),
                            batch_size,
                        )
                    continue

                if dry_run:
                    success_count += 1
                    continue

                buffer.append(result.validated)
                if len(buffer) >= batch_size:
                    inserted, failed_items = self._flush_insert_buffer(
                        session,
                        employees_repo,
                        buffer,
                    )
                    success_count += inserted
                    failed_count += len(failed_items)
                    for item in failed_items:
                        self._queue_error(
                            session,
                            errors_repo,
                            error_buffer,
                            _import_error_from_insert_failure(job_id, item),
                            batch_size,
                        )
                    buffer.clear()

            if buffer:
                inserted, failed_items = self._flush_insert_buffer(
                    session,
                    employees_repo,
                    buffer,
                )
                success_count += inserted
                failed_count += len(failed_items)
                for item in failed_items:
                    self._queue_error(
                        session,
                        errors_repo,
                        error_buffer,
                        _import_error_from_insert_failure(job_id, item),
                        batch_size,
                    )
                buffer.clear()

            try:
                self._flush_error_buffer(session, errors_repo, error_buffer)
            except Exception as exc:
                logger.exception(
                    "Failed to persist import errors for job %s before completion",
                    job_id,
                )
                try:
                    session.rollback()
                except Exception:
                    pass
                self._mark_failed_safe(
                    session,
                    job_id,
                    _sanitize_error_message(
                        f"Import processing failed while saving errors: {exc}"
                    ),
                )
                return

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
            try:
                session.rollback()
            except Exception:
                logger.exception("Rollback failed after CSV parse error for %s", job_id)
            self._persist_parse_issues_safe(session, job_id, exc.issues)
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

    def _queue_error(
        self,
        session: Session,
        errors_repo: ImportErrorRepository,
        error_buffer: list[ImportError],
        error: ImportError,
        batch_size: int,
    ) -> None:
        """Buffer an error; flush+commit in bounded batches (never with employee txns)."""
        error_buffer.append(error)
        if len(error_buffer) >= batch_size:
            self._flush_error_buffer(session, errors_repo, error_buffer)

    def _flush_error_buffer(
        self,
        session: Session,
        errors_repo: ImportErrorRepository,
        error_buffer: list[ImportError],
    ) -> None:
        """
        Persist buffered errors in one add_many + commit.

        Errors live only in the Python list until this call, so employee
        IntegrityError rollbacks cannot erase uncommitted ImportError rows.
        """
        if not error_buffer:
            return
        # Copy before clear so a failed flush does not silently drop the batch.
        batch = list(error_buffer)
        errors_repo.add_many(batch)
        session.commit()
        error_buffer.clear()

    def _persist_parse_issues_safe(
        self,
        session: Session,
        job_id: str,
        issues: Sequence[ImportRowIssue],
    ) -> None:
        """Best-effort persistence of structured CSV parse issues after rollback."""
        if not issues:
            return
        batch_size = self._settings.import_batch_size
        try:
            errors_repo = ImportErrorRepository(session)
            buffer: list[ImportError] = []
            for issue in issues:
                buffer.append(_import_error_from_issue(job_id, issue))
                if len(buffer) >= batch_size:
                    errors_repo.add_many(buffer)
                    session.commit()
                    buffer.clear()
            if buffer:
                errors_repo.add_many(buffer)
                session.commit()
        except Exception:
            logger.exception(
                "Unable to persist CSV parse issues for import job %s",
                job_id,
            )
            try:
                session.rollback()
            except Exception:
                pass

    def _flush_insert_buffer(
        self,
        session: Session,
        employees_repo: EmployeeRepository,
        buffer: Sequence[ValidatedImportRow],
    ) -> tuple[int, list[ValidatedImportRow]]:
        """
        Insert one batch. Returns (success_count, failed_validated_rows).

        On IntegrityError, rolls back the batch and retries each row individually.
        Previously committed batches are unaffected. Does not stage ImportError rows.
        """
        if not buffer:
            return 0, []

        employees = [_employee_from_validated(item) for item in buffer]
        try:
            employees_repo.add_many(employees)
            session.flush()
            session.commit()
            return len(employees), []
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
    ) -> tuple[int, list[ValidatedImportRow]]:
        """
        Insert each row in its own short transaction after a failed batch.

        Returns failed ValidatedImportRow items so callers can persist ImportError
        rows with the original CSV row_number outside this transaction.
        """
        inserted = 0
        failed_items: list[ValidatedImportRow] = []
        for item in buffer:
            try:
                employees_repo.add(_employee_from_validated(item))
                session.flush()
                session.commit()
                inserted += 1
            except IntegrityError:
                session.rollback()
                failed_items.append(item)
        return inserted, failed_items

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
    """
    BackgroundTasks entrypoint: dispatch by ``ImportJob.import_target``.

    EMPLOYEES → employee processor (unchanged).
    COURSES → course processor.
    ENROLMENTS → mark failed (not implemented yet).
    """
    session = session_factory()
    try:
        job = ImportJobRepository(session).get_by_id(job_id)
        if job is None:
            logger.info("Import job %s not found; skipping dispatch", job_id)
            return
        target = job.import_target
    finally:
        session.close()

    if target is ImportTarget.EMPLOYEES:
        ImportProcessingService(session_factory, settings).process(job_id)
        return

    if target is ImportTarget.COURSES:
        from smarthris.services.course_import_processing_service import (
            CourseImportProcessingService,
        )

        CourseImportProcessingService(session_factory, settings).process(job_id)
        return

    if target is ImportTarget.ENROLMENTS:
        _fail_unsupported_target(
            session_factory,
            job_id,
            "Enrolment import processing is not implemented yet",
        )
        return

    _fail_unsupported_target(
        session_factory,
        job_id,
        f"Unsupported import target '{getattr(target, 'value', target)}'",
    )


def _fail_unsupported_target(
    session_factory: Callable[[], Session] | sessionmaker[Session],
    job_id: str,
    message: str,
) -> None:
    session = session_factory()
    try:
        jobs = ImportJobRepository(session)
        job = jobs.get_by_id(job_id)
        if job is None:
            return
        if job.status in {ImportJobStatus.COMPLETED, ImportJobStatus.FAILED}:
            return
        if job.status == ImportJobStatus.PENDING:
            jobs.mark_running(job)
            session.flush()
        jobs.mark_failed(job, error_message=_sanitize_error_message(message))
        session.commit()
    except Exception:
        logger.exception("Unable to mark unsupported-target job %s as failed", job_id)
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()
