"""Phase 3.6 — import error persistence and paginated errors API."""

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
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.importing.validation.row_schema import EmployeeImportRow
from smarthris.importing.validation.row_validator import ValidatedImportRow
from smarthris.main import create_app
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.models.import_error import ImportError
from smarthris.models.import_job import ImportFileType, ImportJobStatus
from smarthris.repositories.employee_repository import EmployeeRepository
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.services.import_processing_service import ImportProcessingService
from tests.auth_helpers import TEST_JWT_SECRET, attach_active_user_override
from tests.test_import_background_processing import _TrackingSessionFactory


def _csv(*rows: str) -> str:
    header = "Employee Number,First Name,Last Name,Work Email,Department ID\n"
    return header + "".join(f"{row}\n" for row in rows)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _error_count(session: Session, job_id: str) -> int:
    return ImportErrorRepository(session).count_for_job(job_id)


def _errors_for_job(session: Session, job_id: str) -> list[ImportError]:
    return ImportErrorRepository(session).list_for_job(job_id, offset=0, limit=10_000)


def _employee_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Employee)) or 0)


@pytest.fixture
def import_settings(tmp_path: Path, integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-Errors",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=200_000,
        import_batch_size=2,
    )


@pytest.fixture
def tracking_factory(db_session: Session) -> _TrackingSessionFactory:
    return _TrackingSessionFactory(db_session)


@pytest.fixture
def import_api_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
    hr_test_user,
) -> TestClient:
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-Errors-API",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=200_000,
        import_batch_size=50,
        jwt_secret_key=TEST_JWT_SECRET,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, hr_test_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_invalid_email_creates_import_error(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="bad-email.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(f"EM-1,Ada,Lovelace,not-an-email,{sample_department.id}"),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    errors = _errors_for_job(db_session, job.id)
    assert len(errors) == 1
    assert errors[0].code == ImportErrorCode.INVALID_EMAIL.value
    assert errors[0].field == "work_email"
    assert errors[0].employee_number == "EM-1"
    assert errors[0].row_number == 2

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.failed_count == 1
    assert loaded.success_count == 0


def test_missing_required_field_creates_import_error(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="missing.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(f",Ada,Lovelace,miss@{job.id[:8]}.example.com,{sample_department.id}"),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    errors = _errors_for_job(db_session, job.id)
    assert any(e.code == ImportErrorCode.MISSING_REQUIRED.value for e in errors)
    assert any(e.field == "employee_number" for e in errors)
    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.failed_count == 1


def test_multiple_issues_one_row_multiple_errors_failed_count_once(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="multi.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    # Missing employee_number + invalid email → multiple issues, one failed row.
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(",Ada,Lovelace,not-an-email,1"),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    errors = _errors_for_job(db_session, job.id)
    assert len(errors) >= 2
    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.failed_count == 1
    assert loaded.success_count == 0


def test_duplicate_in_file_persisted(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="dup-file.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"DF-1,Ada,Lovelace,df1@example.com,{sample_department.id}",
            f"DF-1,Grace,Hopper,df2@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    errors = _errors_for_job(db_session, job.id)
    assert any(e.code == ImportErrorCode.DUPLICATE_IN_FILE.value for e in errors)
    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 1
    assert loaded.failed_count == 1


def test_already_exists_persisted(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    db_session.add(
        Employee(
            employee_number="AE-1",
            first_name="Exist",
            last_name="Ing",
            work_email="ae1@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    db_session.flush()

    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="already.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"AE-1,Ada,Lovelace,ae-new@example.com,{sample_department.id}",
            f"AE-2,Grace,Hopper,ae2@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    errors = _errors_for_job(db_session, job.id)
    assert any(e.code == ImportErrorCode.ALREADY_EXISTS.value for e in errors)
    assert any(e.employee_number == "AE-1" for e in errors)
    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 1
    assert loaded.failed_count == 1


def test_integrity_error_preserves_original_row_numbers(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    db_session.add(
        Employee(
            employee_number="IE6-EXIST",
            first_name="Exist",
            last_name="Ing",
            work_email="ie6-exist@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    db_session.commit()

    service = ImportProcessingService(tracking_factory, import_settings)
    buffer = [
        ValidatedImportRow(
            row_number=7,
            row=EmployeeImportRow(
                employee_number="IE6-EXIST",
                first_name="Clash",
                last_name="Row",
                work_email="ie6-clash@example.com",
                department_id=sample_department.id,
            ),
            resolved_department_id=sample_department.id,
        ),
        ValidatedImportRow(
            row_number=8,
            row=EmployeeImportRow(
                employee_number="IE6-NEW",
                first_name="Ok",
                last_name="Row",
                work_email="ie6-new@example.com",
                department_id=sample_department.id,
            ),
            resolved_department_id=sample_department.id,
        ),
    ]

    inserted, failed_items = service._flush_insert_buffer(
        db_session,
        EmployeeRepository(db_session),
        buffer,
    )
    assert inserted == 1
    assert len(failed_items) == 1
    assert failed_items[0].row_number == 7
    assert failed_items[0].row.employee_number == "IE6-EXIST"


def test_dry_run_persists_errors_inserts_zero(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    before = _employee_count(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="dry-err.csv", file_type=ImportFileType.CSV, dry_run=True)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"DR-OK,Ada,Lovelace,dro@example.com,{sample_department.id}",
            f"DR-BAD,Bad,Email,not-an-email,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 1
    assert loaded.failed_count == 1
    assert _employee_count(db_session) == before
    assert _error_count(db_session, job.id) >= 1


def test_large_error_sets_use_bounded_add_many(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    # batch_size=2 → 5 invalid rows should trigger multiple add_many calls
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="many-err.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    rows = [f"ME-{i},Bad,Email,not-an-email,{i}" for i in range(5)]
    _write(import_storage_path(import_settings.import_temp_dir, job.id), _csv(*rows))

    calls: list[int] = []
    original = ImportErrorRepository.add_many

    def counting_add_many(self, errors):
        calls.append(len(errors))
        return original(self, errors)

    with patch.object(ImportErrorRepository, "add_many", counting_add_many):
        ImportProcessingService(tracking_factory, import_settings).process(job.id)

    assert len(calls) >= 2
    assert all(n <= import_settings.import_batch_size for n in calls)
    assert sum(calls) == _error_count(db_session, job.id)


def test_error_commits_survive_employee_rollback(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    """Committed ImportError rows are not erased by a later employee IntegrityError."""
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="survive.csv", file_type=ImportFileType.CSV)
    db_session.flush()

    errors_repo = ImportErrorRepository(db_session)
    errors_repo.add(
        import_job_id=job.id,
        row_number=2,
        code=ImportErrorCode.INVALID_EMAIL.value,
        message="pre-committed",
        employee_number="PRE-1",
        field="work_email",
    )
    db_session.commit()
    assert _error_count(db_session, job.id) == 1

    db_session.add(
        Employee(
            employee_number="SURV-EXIST",
            first_name="Exist",
            last_name="Ing",
            work_email="surv-exist@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    db_session.commit()

    service = ImportProcessingService(tracking_factory, import_settings)
    buffer = [
        ValidatedImportRow(
            row_number=3,
            row=EmployeeImportRow(
                employee_number="SURV-EXIST",
                first_name="Clash",
                last_name="Row",
                work_email="surv-clash@example.com",
                department_id=sample_department.id,
            ),
            resolved_department_id=sample_department.id,
        ),
    ]
    inserted, failed_items = service._flush_insert_buffer(
        db_session,
        EmployeeRepository(db_session),
        buffer,
    )
    assert inserted == 0
    assert len(failed_items) == 1
    # Prior committed error still present after insert rollback.
    assert _error_count(db_session, job.id) == 1
    assert _errors_for_job(db_session, job.id)[0].message == "pre-committed"


def test_csv_parse_error_persists_structured_issues(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="bad-headers.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        "Totally,Wrong,Headers\nx,y,z\n",
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.FAILED
    assert loaded.error_message is not None
    assert "CSV parse" in loaded.error_message
    assert _error_count(db_session, job.id) >= 1


def test_get_errors_pagination_and_ordering(
    db_session: Session,
    import_api_client: TestClient,
) -> None:
    jobs = ImportJobRepository(db_session)
    errors = ImportErrorRepository(db_session)
    job = jobs.create(filename="page.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    for row_number in (5, 2, 2, 9):
        errors.add(
            import_job_id=job.id,
            row_number=row_number,
            code="invalid_email",
            message=f"row {row_number}",
            employee_number=f"E{row_number}",
            field="work_email",
        )
    db_session.commit()

    response = import_api_client.get(f"/imports/{job.id}/errors?page=1&page_size=2")
    assert response.status_code == 200
    body = response.json()
    assert body["import_job_id"] == job.id
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 2
    # Deterministic: row_number asc, then id
    assert body["items"][0]["row_number"] == 2
    assert body["items"][1]["row_number"] == 2
    assert body["items"][0]["id"] < body["items"][1]["id"]

    page2 = import_api_client.get(f"/imports/{job.id}/errors?page=2&page_size=2").json()
    assert [item["row_number"] for item in page2["items"]] == [5, 9]


def test_get_errors_404_unknown_job(import_api_client: TestClient) -> None:
    response = import_api_client.get(
        "/imports/00000000-0000-0000-0000-000000000099/errors"
    )
    assert response.status_code == 404


def test_get_errors_scoped_to_job(
    db_session: Session,
    import_api_client: TestClient,
) -> None:
    jobs = ImportJobRepository(db_session)
    errors = ImportErrorRepository(db_session)
    job_a = jobs.create(filename="a.csv", file_type=ImportFileType.CSV)
    job_b = jobs.create(filename="b.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    errors.add(
        import_job_id=job_a.id,
        row_number=2,
        code="invalid_email",
        message="a only",
        employee_number="A1",
        field="work_email",
    )
    errors.add(
        import_job_id=job_b.id,
        row_number=2,
        code="invalid_email",
        message="b only",
        employee_number="B1",
        field="work_email",
    )
    db_session.commit()

    body = import_api_client.get(f"/imports/{job_a.id}/errors").json()
    assert body["total"] == 1
    assert body["items"][0]["message"] == "a only"
    assert body["items"][0]["employee_number"] == "A1"


def test_successful_import_empty_errors_list(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    import_api_client: TestClient,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="ok.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(f"OK-1,Ada,Lovelace,ok1-{job.id[:8]}@example.com,{sample_department.id}"),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    body = import_api_client.get(f"/imports/{job.id}/errors").json()
    assert body["total"] == 0
    assert body["items"] == []
    assert body["pages"] == 0
