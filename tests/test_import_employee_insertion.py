"""Phase 3.5 — batch employee insertion during background import."""

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
from smarthris.importing.validation.row_schema import EmployeeImportRow
from smarthris.importing.validation.row_validator import ValidatedImportRow
from smarthris.main import create_app
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.models.import_job import ImportFileType, ImportJobStatus
from smarthris.repositories.employee_repository import EmployeeRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.services.import_processing_service import ImportProcessingService
from tests.test_import_background_processing import _TrackingSessionFactory


def _csv(*rows: str) -> str:
    header = "Employee Number,First Name,Last Name,Work Email,Department ID\n"
    return header + "".join(f"{row}\n" for row in rows)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def import_settings(tmp_path: Path, integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-Insert",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=200_000,
        import_batch_size=2,
    )


@pytest.fixture
def tracking_factory(db_session: Session) -> _TrackingSessionFactory:
    return _TrackingSessionFactory(db_session)


def _employee_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(Employee)) or 0)


def test_valid_csv_inserts_employees(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    before = _employee_count(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="ok.csv", file_type=ImportFileType.CSV, dry_run=False)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"INS-1,Ada,Lovelace,ins1@example.com,{sample_department.id}",
            f"INS-2,Grace,Hopper,ins2@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.COMPLETED
    assert loaded.success_count == 2
    assert loaded.failed_count == 0
    assert _employee_count(db_session) == before + 2

    repo = EmployeeRepository(db_session)
    assert repo.find_existing_employee_numbers(["INS-1", "INS-2"]) == {"INS-1", "INS-2"}
    emp = db_session.scalars(select(Employee).where(Employee.employee_number == "INS-1")).one()
    assert emp.manager_employee_id is None
    assert emp.department_id == sample_department.id


def test_dry_run_inserts_zero_employees(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    before = _employee_count(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="dry.csv", file_type=ImportFileType.CSV, dry_run=True)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(f"DRY-1,Ada,Lovelace,dry1@example.com,{sample_department.id}"),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 1
    assert _employee_count(db_session) == before


def test_mixed_valid_invalid_counters_and_inserts(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    before = _employee_count(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="mixed.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"MIX-1,Ada,Lovelace,mix1@example.com,{sample_department.id}",
            f"MIX-2,Bad,Email,not-an-email,{sample_department.id}",
            f"MIX-3,Alan,Turing,mix3@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.COMPLETED
    assert loaded.total_rows == 3
    assert loaded.success_count == 2
    assert loaded.failed_count == 1
    assert _employee_count(db_session) == before + 2


def test_existing_duplicate_not_inserted(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    db_session.add(
        Employee(
            employee_number="DUP-1",
            first_name="Exist",
            last_name="Ing",
            work_email="dup1@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    db_session.flush()
    before = _employee_count(db_session)

    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="dup.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"DUP-1,Ada,Lovelace,new-dup@example.com,{sample_department.id}",
            f"DUP-2,Grace,Hopper,dup2@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 1
    assert loaded.failed_count == 1
    assert _employee_count(db_session) == before + 1


def test_unknown_department_not_inserted(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    before = _employee_count(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="nodept.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv("ND-1,Ada,Lovelace,nd1@example.com,999999"),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 0
    assert loaded.failed_count == 1
    assert _employee_count(db_session) == before


def test_batch_size_plus_one_uses_multiple_batches(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    # import_settings.import_batch_size == 2 → 3 rows => 2 flushes
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="batches.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"B-1,Ada,Lovelace,b1@example.com,{sample_department.id}",
            f"B-2,Grace,Hopper,b2@example.com,{sample_department.id}",
            f"B-3,Alan,Turing,b3@example.com,{sample_department.id}",
        ),
    )

    flush_calls = {"n": 0}
    original = ImportProcessingService._flush_insert_buffer

    def counting_flush(self, session, employees_repo, buffer):
        flush_calls["n"] += 1
        return original(self, session, employees_repo, buffer)

    with patch.object(ImportProcessingService, "_flush_insert_buffer", counting_flush):
        ImportProcessingService(tracking_factory, import_settings).process(job.id)

    assert flush_calls["n"] == 2
    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.success_count == 3


def test_integrity_error_retries_individually(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    """Batch IntegrityError rolls back; unique row still inserts via savepoint retry."""
    db_session.add(
        Employee(
            employee_number="IE-EXIST",
            first_name="Exist",
            last_name="Ing",
            work_email="ie-exist@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    # Commit so a later batch rollback cannot undo this row (savepoint-aware tests).
    db_session.commit()
    before = _employee_count(db_session)

    service = ImportProcessingService(tracking_factory, import_settings)
    buffer = [
        ValidatedImportRow(
            row_number=2,
            row=EmployeeImportRow(
                employee_number="IE-EXIST",
                first_name="Clash",
                last_name="Row",
                work_email="ie-clash@example.com",
                department_id=sample_department.id,
            ),
            resolved_department_id=sample_department.id,
        ),
        ValidatedImportRow(
            row_number=3,
            row=EmployeeImportRow(
                employee_number="IE-NEW",
                first_name="Ok",
                last_name="Row",
                work_email="ie-new@example.com",
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
    assert failed_items[0].row_number == 2
    assert failed_items[0].row.employee_number == "IE-EXIST"
    assert _employee_count(db_session) == before + 1
    assert EmployeeRepository(db_session).find_existing_employee_numbers(["IE-NEW"]) == {
        "IE-NEW"
    }


def test_prior_batch_remains_after_later_batch_conflict(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    """Committed batch 1 survives when batch 2 hits conflicts."""
    db_session.add(
        Employee(
            employee_number="PB-CONFLICT",
            first_name="Exist",
            last_name="Ing",
            work_email="pb-conflict@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    db_session.flush()

    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="prior.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    # batch_size=2: first batch PB-1, PB-2; second PB-CONFLICT, PB-3
    _write(
        import_storage_path(import_settings.import_temp_dir, job.id),
        _csv(
            f"PB-1,Ada,Lovelace,pb1@example.com,{sample_department.id}",
            f"PB-2,Grace,Hopper,pb2@example.com,{sample_department.id}",
            f"PB-CONFLICT,Clash,Row,pb-new-email@example.com,{sample_department.id}",
            f"PB-3,Alan,Turing,pb3@example.com,{sample_department.id}",
        ),
    )

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    loaded = jobs.get_by_id(job.id)
    assert loaded is not None
    assert loaded.status == ImportJobStatus.COMPLETED
    assert loaded.success_count == 3
    assert loaded.failed_count == 1
    numbers = EmployeeRepository(db_session).find_existing_employee_numbers(
        ["PB-1", "PB-2", "PB-3", "PB-CONFLICT"]
    )
    assert numbers == {"PB-1", "PB-2", "PB-3", "PB-CONFLICT"}


def test_manager_employee_number_still_null_manager_id(
    db_session: Session,
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
    sample_department,
) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="mgr.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    body = (
        "Employee Number,First Name,Last Name,Work Email,Department ID,"
        "Manager Employee Number\n"
        f"MGR-1,Ada,Lovelace,mgr1@example.com,{sample_department.id},E-BOSS\n"
    )
    _write(import_storage_path(import_settings.import_temp_dir, job.id), body)

    ImportProcessingService(tracking_factory, import_settings).process(job.id)

    emp = db_session.scalars(select(Employee).where(Employee.employee_number == "MGR-1")).one()
    assert emp.manager_employee_id is None


def test_session_closed_after_process(
    tracking_factory: _TrackingSessionFactory,
    import_settings: Settings,
) -> None:
    ImportProcessingService(tracking_factory, import_settings).process(
        "00000000-0000-0000-0000-000000000099"
    )
    assert tracking_factory.open_count == 1
    assert tracking_factory.close_count == 1


def test_employee_crud_still_works(
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
                "employee_number": "E-CRUD-3-5",
                "first_name": "Crud",
                "last_name": "Ok",
                "work_email": "crud35@example.com",
                "department_id": sample_department.id,
            },
        )
    assert response.status_code == 201
    app.dependency_overrides.clear()
