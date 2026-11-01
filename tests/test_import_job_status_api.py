"""Phase 3.7 — GET /imports/{job_id} status API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.main import create_app
from smarthris.models.import_job import ImportFileType, ImportJobStatus
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.schemas.import_job import ImportJobRead


_IMPORT_JOB_READ_FIELDS = set(ImportJobRead.model_fields)


@pytest.fixture
def import_api_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-Status-API",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=200_000,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_get_completed_job_returns_final_counters(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="done.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(job)
    jobs.update_counts(
        job,
        total_rows=10,
        processed_rows=10,
        success_count=8,
        failed_count=2,
    )
    jobs.mark_completed(job)
    db_session.commit()

    response = import_api_client.get(f"/imports/{job.id}")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == _IMPORT_JOB_READ_FIELDS
    assert body["id"] == job.id
    assert body["status"] == ImportJobStatus.COMPLETED.value
    assert body["filename"] == "done.csv"
    assert body["file_type"] == "csv"
    assert body["dry_run"] is False
    assert body["total_rows"] == 10
    assert body["processed_rows"] == 10
    assert body["success_count"] == 8
    assert body["failed_count"] == 2
    assert body["error_message"] is None
    assert body["created_at"] is not None
    assert body["started_at"] is not None
    assert body["finished_at"] is not None


def test_get_unknown_job_returns_404(import_api_client: TestClient) -> None:
    response = import_api_client.get(
        "/imports/00000000-0000-0000-0000-000000000099"
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert "not found" in body["detail"].lower()


def test_get_pending_job_initial_counters(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="pending.csv", file_type=ImportFileType.CSV)
    db_session.commit()

    body = import_api_client.get(f"/imports/{job.id}").json()
    assert body["status"] == ImportJobStatus.PENDING.value
    assert body["total_rows"] == 0
    assert body["processed_rows"] == 0
    assert body["success_count"] == 0
    assert body["failed_count"] == 0
    assert body["started_at"] is None
    assert body["finished_at"] is None


def test_get_running_job_has_started_at(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="running.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(job)
    db_session.commit()

    body = import_api_client.get(f"/imports/{job.id}").json()
    assert body["status"] == ImportJobStatus.RUNNING.value
    assert body["started_at"] is not None
    assert body["finished_at"] is None


def test_get_failed_job_returns_error_message(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="failed.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(job)
    jobs.mark_failed(job, error_message="CSV parse error: bad headers")
    db_session.commit()

    body = import_api_client.get(f"/imports/{job.id}").json()
    assert body["status"] == ImportJobStatus.FAILED.value
    assert body["error_message"] == "CSV parse error: bad headers"
    assert body["finished_at"] is not None


def test_get_dry_run_job_preserves_flag(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="dry.csv",
        file_type=ImportFileType.CSV,
        dry_run=True,
    )
    db_session.commit()

    body = import_api_client.get(f"/imports/{job.id}").json()
    assert body["dry_run"] is True


def test_get_job_is_read_only(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="readonly.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(job)
    jobs.update_counts(
        job,
        total_rows=3,
        processed_rows=1,
        success_count=1,
        failed_count=0,
    )
    db_session.commit()

    before = jobs.get_by_id(job.id)
    assert before is not None
    snapshot = (
        before.status,
        before.total_rows,
        before.processed_rows,
        before.success_count,
        before.failed_count,
        before.error_message,
        before.created_at,
        before.started_at,
        before.finished_at,
    )

    assert import_api_client.get(f"/imports/{job.id}").status_code == 200
    assert import_api_client.get(f"/imports/{job.id}").status_code == 200

    db_session.refresh(before)
    after = (
        before.status,
        before.total_rows,
        before.processed_rows,
        before.success_count,
        before.failed_count,
        before.error_message,
        before.created_at,
        before.started_at,
        before.finished_at,
    )
    assert after == snapshot


def test_get_job_and_errors_routes_coexist(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    errors = ImportErrorRepository(db_session)
    job = jobs.create(filename="both.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    errors.add(
        import_job_id=job.id,
        row_number=2,
        code="invalid_email",
        message="bad email",
        employee_number="E1",
        field="work_email",
    )
    db_session.commit()

    job_response = import_api_client.get(f"/imports/{job.id}")
    assert job_response.status_code == 200
    assert job_response.json()["id"] == job.id

    errors_response = import_api_client.get(f"/imports/{job.id}/errors")
    assert errors_response.status_code == 200
    errors_body = errors_response.json()
    assert errors_body["import_job_id"] == job.id
    assert errors_body["total"] == 1
    assert errors_body["items"][0]["code"] == "invalid_email"


def test_response_matches_import_job_read_field_names(
    import_api_client: TestClient,
    db_session: Session,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="schema.csv", file_type=ImportFileType.CSV)
    db_session.commit()

    body = import_api_client.get(f"/imports/{job.id}").json()
    assert "filename" in body
    assert "processed_rows" in body
    assert "finished_at" in body
    assert "original_filename" not in body
    assert "processed_count" not in body
    assert "completed_at" not in body
    assert "path" not in body
    ImportJobRead.model_validate(body)
