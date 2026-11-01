"""Phase 3.4 — background import validation processing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.importing.storage import import_storage_path
from smarthris.main import create_app
from smarthris.models.employee import Employee
from smarthris.models.import_job import ImportFileType, ImportJobStatus
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.services.import_processing_service import (
    ImportProcessingService,
    process_import_job,
)


class _TrackingSessionFactory:
    """Return the test session but record open/close without closing it."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self.open_count = 0
        self.close_count = 0

    def __call__(self) -> Session:
        self.open_count += 1
        factory = self
        session = self._session

        class _Guard:
            def __getattr__(self, name: str):
                return getattr(session, name)

            def close(self) -> None:
                factory.close_count += 1

            def commit(self):
                return session.commit()

            def rollback(self):
                return session.rollback()

        return _Guard()  # type: ignore[return-value]


@pytest.fixture
def import_settings(tmp_path: Path, integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-BG",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=100_000,
        import_batch_size=50,
    )


@pytest.fixture
def tracking_factory(db_session: Session) -> _TrackingSessionFactory:
    return _TrackingSessionFactory(db_session)


def _write_csv(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _valid_csv_body(*rows: str) -> str:
    header = "Employee Number,First Name,Last Name,Work Email,Department ID\n"
    return header + "".join(f"{row}\n" for row in rows)


def _create_pending_job(
    db_session: Session,
    settings: Settings,
    *,
    dry_run: bool = False,
    csv_body: str | None = None,
    job_id: str | None = None,
) -> str:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="employees.csv",
        file_type=ImportFileType.CSV,
        dry_run=dry_run,
        job_id=job_id,
    )
    db_session.flush()
    body = csv_body or _valid_csv_body(
        f"E-{job.id[:8]},Ada,Lovelace,ada-{job.id[:8]}@example.com,1"
    )
    # Department id 1 may not exist — use sample_department in tests that need valid rows.
    _write_csv(import_storage_path(settings.import_temp_dir, job.id), body)
    return job.id


def test_process_unknown_job_is_safe(
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    ImportProcessingService(tracking_factory, import_settings).process(
        "00000000-0000-0000-0000-000000000000"
    )
    assert tracking_factory.open_count == 1
    assert tracking_factory.close_count == 1


def test_process_successful_validation(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="ok.csv", file_type=ImportFileType.CSV, dry_run=False)
    db_session.flush()
    _write_csv(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _valid_csv_body(
            f"E1,Ada,Lovelace,ada1@example.com,{sample_department.id}",
            f"E2,Grace,Hopper,grace@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.COMPLETED
    assert loaded.total_rows == 2
    assert loaded.processed_rows == 2
    assert loaded.success_count == 2
    assert loaded.failed_count == 0
    assert tracking_factory.close_count == 1


def test_process_invalid_rows_update_counters_not_failed_status(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="mixed.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write_csv(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _valid_csv_body(
            f"E1,Ada,Lovelace,ada-ok@example.com,{sample_department.id}",
            f"E2,Bad,Email,not-an-email,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.COMPLETED
    assert loaded.total_rows == 2
    assert loaded.success_count == 1
    assert loaded.failed_count == 1


def test_process_missing_file_marks_failed(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="missing.csv", file_type=ImportFileType.CSV)
    db_session.flush()

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.FAILED
    assert loaded.error_message is not None
    assert "not found" in loaded.error_message.lower()
    assert str(import_settings.import_temp_dir) not in loaded.error_message


def test_process_malformed_csv_marks_failed(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="bad.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write_csv(
        import_storage_path(import_settings.import_temp_dir, job.id),
        'Employee Number,First Name,Last Name,Work Email,Department ID\nE1,"Ada\n',
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.FAILED
    assert loaded.error_message is not None
    assert "csv" in loaded.error_message.lower()


def test_status_guard_skips_non_pending(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="once.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write_csv(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _valid_csv_body(
            f"E1,Ada,Lovelace,once@example.com,{sample_department.id}",
        ),
    )

    service = ImportProcessingService(tracking_factory, import_settings)
    service.process(job.id)
    first = jobs.get_by_id(job.id)
    assert first is not None
    assert first.status == ImportJobStatus.COMPLETED
    assert first.success_count == 1

    # Second run must not re-process or change counters.
    service.process(job.id)
    second = jobs.get_by_id(job.id)
    assert second is not None
    assert second.status == ImportJobStatus.COMPLETED
    assert second.success_count == 1
    assert second.processed_rows == 1


def test_dry_run_true_and_false_validate_only(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    before = int(db_session.scalar(select(func.count()).select_from(Employee)) or 0)

    for dry_run in (True, False):
        jobs = ImportJobRepository(db_session)
        job = jobs.create(
            filename=f"dry-{dry_run}.csv",
            file_type=ImportFileType.CSV,
            dry_run=dry_run,
        )
        db_session.flush()
        _write_csv(
            import_storage_path(import_settings.import_temp_dir, job.id),
            _valid_csv_body(
                f"E-{dry_run},Ada,Lovelace,dry-{dry_run}@example.com,{sample_department.id}",
            ),
        )
        ImportProcessingService(tracking_factory, import_settings).process(job.id)
        loaded = jobs.get_by_id(job.id)
        assert loaded is not None
        assert loaded.status == ImportJobStatus.COMPLETED
        assert loaded.dry_run is dry_run
        assert loaded.success_count == 1

    after = int(db_session.scalar(select(func.count()).select_from(Employee)) or 0)
    assert after == before


def test_fatal_exception_marks_failed(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="boom.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write_csv(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _valid_csv_body(
            f"E1,Ada,Lovelace,boom@example.com,{sample_department.id}",
        ),
    )

    with patch(
        "smarthris.services.import_processing_service.ImportRowValidator.validate",
        side_effect=RuntimeError("validator exploded"),
    ):
        ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.FAILED
    assert loaded.error_message is not None
    assert "validator exploded" in loaded.error_message


def test_process_import_job_entrypoint_uses_factory(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="entry.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write_csv(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _valid_csv_body(
            f"E9,Ada,Lovelace,entry@example.com,{sample_department.id}",
        ),
    )

    process_import_job(
        job.id,
        session_factory=tracking_factory,
        settings=import_settings,
    )
    assert tracking_factory.open_count >= 1
    assert tracking_factory.close_count >= 1
    assert jobs.get_by_id(job.id).status == ImportJobStatus.COMPLETED


def test_upload_schedules_background_task(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=50_000,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    with patch(
        "smarthris.api.routers.imports.process_import_job",
        autospec=True,
    ) as mock_process:
        with TestClient(app) as client:
            response = client.post(
                "/imports",
                files={
                    "file": (
                        "employees.csv",
                        _valid_csv_body("E1,Ada,Lovelace,a@example.com,1").encode(),
                        "text/csv",
                    )
                },
            )
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == ImportJobStatus.PENDING.value
        mock_process.assert_called_once()
        args, kwargs = mock_process.call_args
        assert args[0] == body["id"]
        assert kwargs["settings"] is app.state.settings
        assert kwargs["session_factory"] is app.state.db_session_factory

    app.dependency_overrides.clear()


def test_employee_api_unaffected(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
    sample_department,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
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
        response = client.post(
            "/employees",
            json={
                "employee_number": "E-BG-SMOKE",
                "first_name": "Bg",
                "last_name": "Smoke",
                "work_email": "bg.smoke@example.com",
                "department_id": sample_department.id,
            },
        )
    assert response.status_code == 201
    app.dependency_overrides.clear()
