"""Background validation and batch insertion of enrolment import jobs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from smarthris.config import Settings
from smarthris.importing.enrolment_column_mapping import enrolment_record_key
from smarthris.importing.parsers.csv_parser import CsvParseError
from smarthris.importing.parsers.enrolment_csv_parser import parse_enrolment_csv
from smarthris.importing.storage import import_storage_path
from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.enrolment_row_validator import (
    EnrolmentImportRowValidator,
    ValidatedEnrolmentImportRow,
)
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.models.course_enrolment import CourseEnrolment
from smarthris.models.import_error import ImportError
from smarthris.models.import_job import ImportJobStatus
from smarthris.repositories.course_enrolment_repository import CourseEnrolmentRepository
from smarthris.repositories.course_repository import CourseRepository
from smarthris.repositories.employee_repository import EmployeeRepository
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_LEN = 1000


def _sanitize_error_message(message: str) -> str:
    text = " ".join(str(message).split())
    if len(text) > _MAX_ERROR_MESSAGE_LEN:
        text = text[: _MAX_ERROR_MESSAGE_LEN - 3] + "..."
    return text


def _enrolment_from_validated(item: ValidatedEnrolmentImportRow) -> CourseEnrolment:
    row = item.row
    return CourseEnrolment(
        employee_id=item.employee_id,
        course_id=item.course_id,
        enrolment_date=row.enrolment_date,
        completion_date=row.completion_date,
        status=row.status,
        score=row.score,
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


def _import_error_from_insert_failure(
    job_id: str,
    item: ValidatedEnrolmentImportRow,
) -> ImportError:
    key = enrolment_record_key(item.row.employee_number, item.row.course_code)
    return ImportError(
        import_job_id=job_id,
        row_number=item.row_number,
        employee_number=item.row.employee_number,
        record_key=key,
        field="employee_number",
        code=ImportErrorCode.ALREADY_EXISTS.value,
        message=(
            f"Enrolment '{key}' could not be inserted due to a unique constraint conflict"
        ),
    )


class EnrolmentImportProcessingService:
    """Validate uploaded enrolment CSV rows and insert enrolments in batches."""

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
            buffer: list[ValidatedEnrolmentImportRow] = []
            error_buffer: list[ImportError] = []
            batch_size = self._settings.import_batch_size
            employees_repo = EmployeeRepository(session)
            courses_repo = CourseRepository(session)
            enrolments_repo = CourseEnrolmentRepository(session)
            errors_repo = ImportErrorRepository(session)

            validator = EnrolmentImportRowValidator(
                employee_repository=employees_repo,
                course_repository=courses_repo,
                enrolment_repository=enrolments_repo,
                batch_size=batch_size,
            )
            for result in validator.validate(parse_enrolment_csv(csv_path)):
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
                        enrolments_repo,
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
                    enrolments_repo,
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
                    "Failed to persist import errors for enrolment job %s before completion",
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
                logger.error("Import job %s disappeared during enrolment processing", job_id)
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
            logger.warning(
                "CSV parse failed for enrolment import job %s: %s",
                job_id,
                exc.message,
            )
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
            logger.exception("Unexpected failure processing enrolment import job %s", job_id)
            try:
                session.rollback()
            except Exception:
                logger.exception("Rollback failed for enrolment import job %s", job_id)
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
        error_buffer.append(error)
        if len(error_buffer) >= batch_size:
            self._flush_error_buffer(session, errors_repo, error_buffer)

    def _flush_error_buffer(
        self,
        session: Session,
        errors_repo: ImportErrorRepository,
        error_buffer: list[ImportError],
    ) -> None:
        if not error_buffer:
            return
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
                "Unable to persist CSV parse issues for enrolment import job %s",
                job_id,
            )
            try:
                session.rollback()
            except Exception:
                pass

    def _flush_insert_buffer(
        self,
        session: Session,
        enrolments_repo: CourseEnrolmentRepository,
        buffer: Sequence[ValidatedEnrolmentImportRow],
    ) -> tuple[int, list[ValidatedEnrolmentImportRow]]:
        if not buffer:
            return 0, []

        enrolments = [_enrolment_from_validated(item) for item in buffer]
        try:
            enrolments_repo.add_many(enrolments)
            session.flush()
            session.commit()
            return len(enrolments), []
        except IntegrityError:
            logger.info(
                "Enrolment batch insert hit IntegrityError for %s rows; retrying individually",
                len(buffer),
            )
            session.rollback()
            return self._insert_rows_individually(session, enrolments_repo, buffer)

    def _insert_rows_individually(
        self,
        session: Session,
        enrolments_repo: CourseEnrolmentRepository,
        buffer: Sequence[ValidatedEnrolmentImportRow],
    ) -> tuple[int, list[ValidatedEnrolmentImportRow]]:
        inserted = 0
        failed_items: list[ValidatedEnrolmentImportRow] = []
        for item in buffer:
            try:
                enrolments_repo.add(_enrolment_from_validated(item))
                session.flush()
                session.commit()
                inserted += 1
            except IntegrityError:
                session.rollback()
                failed_items.append(item)
        return inserted, failed_items

    def _mark_failed_safe(self, session: Session, job_id: str, error_message: str) -> None:
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
            logger.exception("Unable to mark enrolment import job %s as failed", job_id)
            try:
                session.rollback()
            except Exception:
                pass
            return False
