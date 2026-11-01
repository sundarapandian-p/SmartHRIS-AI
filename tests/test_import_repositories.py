"""Integration tests for bulk import job and error repositories."""

import pytest
from sqlalchemy.orm import Session

from smarthris.models.import_error import ImportError
from smarthris.models.import_job import ImportFileType, ImportJobStatus
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository


@pytest.mark.usefixtures("integration_engine")
def test_create_import_job_with_uuid(db_session: Session) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="employees.csv", file_type=ImportFileType.CSV, dry_run=True)
    db_session.flush()

    assert len(job.id) == 36
    assert job.status == ImportJobStatus.PENDING
    assert job.dry_run is True
    assert jobs.get_by_id(job.id) is not None


@pytest.mark.usefixtures("integration_engine")
def test_update_job_status_and_counts(db_session: Session) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="batch.xlsx", file_type=ImportFileType.XLSX)
    jobs.mark_running(job)
    jobs.update_counts(job, total_rows=100, processed_rows=50, success_count=45, failed_count=5)
    jobs.mark_completed(job)
    db_session.flush()

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.COMPLETED
    assert loaded.total_rows == 100
    assert loaded.success_count == 45
    assert loaded.failed_count == 5
    assert loaded.started_at is not None
    assert loaded.finished_at is not None


@pytest.mark.usefixtures("integration_engine")
def test_import_errors_persist_and_paginate(db_session: Session) -> None:
    jobs = ImportJobRepository(db_session)
    errors = ImportErrorRepository(db_session)

    job = jobs.create(filename="bad.csv", file_type=ImportFileType.CSV)
    errors.add(
        import_job_id=job.id,
        row_number=2,
        code="duplicate_in_file",
        message="Duplicate employee number",
        employee_number="E002",
        field="employee_number",
    )
    errors.add_many(
        [
            ImportError(
                import_job_id=job.id,
                row_number=5,
                code="invalid_email",
                message="Invalid email format",
                employee_number="E005",
                field="work_email",
            )
        ]
    )
    db_session.flush()

    assert errors.count_for_job(job.id) == 2
    page = errors.list_for_job(job.id, offset=0, limit=10)
    assert len(page) == 2
    assert page[0].row_number == 2


@pytest.mark.usefixtures("integration_engine")
def test_import_settings_defaults() -> None:
    from smarthris.config import Settings

    settings = Settings(_env_file=None)
    assert settings.import_batch_size == 500
    assert settings.max_upload_bytes == 52_428_800
    assert settings.import_temp_dir == "data/imports"
