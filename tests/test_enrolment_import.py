"""Phase 5.3.2D2 — enrolment CSV import tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.importing.enrolment_column_mapping import (
    enrolment_record_key,
    map_enrolment_headers,
    resolve_canonical_header,
)
from smarthris.importing.parsers.enrolment_csv_parser import parse_enrolment_csv
from smarthris.importing.storage import import_storage_path
from smarthris.importing.validation.enrolment_row_schema import parse_enrolment_import_row
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.main import create_app
from smarthris.models.course import Course, CourseStatus
from smarthris.models.course_enrolment import CourseEnrolment, CourseEnrolmentStatus
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.models.import_job import ImportFileType, ImportJobStatus, ImportTarget
from smarthris.repositories.course_enrolment_repository import CourseEnrolmentRepository
from smarthris.repositories.course_repository import CourseRepository
from smarthris.repositories.employee_repository import EmployeeRepository
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.services.enrolment_import_processing_service import (
    EnrolmentImportProcessingService,
)
from smarthris.services.import_processing_service import process_import_job
from tests.auth_helpers import TEST_JWT_SECRET, attach_active_user_override


class _TrackingSessionFactory:
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
def enrolment_settings(tmp_path: Path, integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Enrolment-Import",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=100_000,
        import_batch_size=50,
        jwt_secret_key=TEST_JWT_SECRET,
    )


@pytest.fixture
def enrolment_api_client(
    db_session: Session,
    enrolment_settings: Settings,
    hr_test_user,
) -> TestClient:
    app = create_app(enrolment_settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, hr_test_user)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _enrolment_csv(*rows: str, header: str | None = None) -> str:
    hdr = header or (
        "employee_number,course_code,enrolment_date,status,completion_date,score"
    )
    return hdr + "\n" + "\n".join(rows) + "\n"


def _write_job_csv(settings: Settings, job_id: str, body: str) -> Path:
    path = import_storage_path(settings.import_temp_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _seed_employee_and_course(
    db_session: Session,
    sample_department,
    *,
    employee_number: str = "EMP0001",
    course_code: str = "PYTHON101",
) -> tuple[Employee, Course]:
    employee = Employee(
        employee_number=employee_number,
        first_name="Ada",
        last_name="Lovelace",
        work_email=f"{employee_number.lower()}@example.com",
        department_id=sample_department.id,
        status=EmployeeStatus.ACTIVE,
    )
    course = Course(
        course_code=course_code,
        course_name="Python 101",
        category="Engineering",
        duration_hours=Decimal("10.00"),
        status=CourseStatus.ACTIVE,
    )
    db_session.add_all([employee, course])
    db_session.flush()
    return employee, course


def test_enrolment_header_aliases() -> None:
    assert resolve_canonical_header("Employee Number") == "employee_number"
    assert resolve_canonical_header("Course Code") == "course_code"
    assert resolve_canonical_header("Enrollment Date") == "enrolment_date"
    assert resolve_canonical_header("Completion Date") == "completion_date"
    mapping = map_enrolment_headers(
        [
            "Employee Number",
            "Course Code",
            "Enrolment Date",
            "Status",
            "Completion Date",
            "Score",
        ]
    )
    assert mapping.is_valid


def test_enrolment_headers_missing_required() -> None:
    mapping = map_enrolment_headers(["employee_number", "course_code"])
    assert not mapping.is_valid
    fields = {issue.field for issue in mapping.issues}
    assert "enrolment_date" in fields
    assert "status" in fields


def test_parse_enrolment_row_valid_and_completion_rules() -> None:
    row, issues = parse_enrolment_import_row(
        2,
        {
            "employee_number": "EMP0001",
            "course_code": "PYTHON101",
            "enrolment_date": "2026-01-10",
            "status": "completed",
            "completion_date": "2026-02-01",
            "score": "95.50",
        },
    )
    assert not issues
    assert row is not None
    assert row.status is CourseEnrolmentStatus.COMPLETED
    assert row.score == Decimal("95.50")

    _, missing = parse_enrolment_import_row(
        3,
        {
            "employee_number": "EMP0001",
            "course_code": "PYTHON101",
            "enrolment_date": "2026-01-10",
            "status": "completed",
            "completion_date": "",
            "score": "",
        },
    )
    assert any(i.code == ImportErrorCode.MISSING_REQUIRED.value for i in missing)

    _, early = parse_enrolment_import_row(
        4,
        {
            "employee_number": "EMP0001",
            "course_code": "PYTHON101",
            "enrolment_date": "2026-05-01",
            "status": "completed",
            "completion_date": "2026-04-01",
            "score": "10",
        },
    )
    assert any(i.code == ImportErrorCode.INVALID_DATE.value for i in early)


def test_parse_enrolment_status_and_score_bounds() -> None:
    _, bad_status = parse_enrolment_import_row(
        2,
        {
            "employee_number": "EMP0001",
            "course_code": "PYTHON101",
            "enrolment_date": "2026-01-10",
            "status": "done",
            "completion_date": "",
            "score": "",
        },
    )
    assert any(i.code == ImportErrorCode.INVALID_STATUS.value for i in bad_status)

    _, bad_score = parse_enrolment_import_row(
        3,
        {
            "employee_number": "EMP0001",
            "course_code": "PYTHON101",
            "enrolment_date": "2026-01-10",
            "status": "enrolled",
            "completion_date": "",
            "score": "100.01",
        },
    )
    assert bad_score


def test_enrolment_csv_parser_streams() -> None:
    body = _enrolment_csv(
        "EMP0001,PYTHON101,2026-01-10,enrolled,,",
        header="Employee Number,Course Code,Enrolment Date,Status,Completion Date,Score",
    )
    rows = list(parse_enrolment_csv(StringIO(body)))
    assert len(rows) == 1
    assert rows[0].values["employee_number"] == "EMP0001"
    assert rows[0].values["course_code"] == "PYTHON101"


def test_enrolment_record_key() -> None:
    assert enrolment_record_key("EMP0001", "PYTHON101") == "EMP0001|PYTHON101"
    max_key = enrolment_record_key("E" * 50, "C" * 50)
    assert len(max_key) == 101
    assert max_key == ("E" * 50) + "|" + ("C" * 50)


def test_enrolment_import_success_and_duplicates(
    db_session: Session,
    sample_department,
    enrolment_settings: Settings,
) -> None:
    employee, course = _seed_employee_and_course(db_session, sample_department)
    other = Employee(
        employee_number="EMP0002",
        first_name="Grace",
        last_name="Hopper",
        work_email="emp0002@example.com",
        department_id=sample_department.id,
        status=EmployeeStatus.ACTIVE,
    )
    db_session.add(other)
    db_session.flush()

    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()
    _write_job_csv(
        enrolment_settings,
        job.id,
        _enrolment_csv(
            "EMP0001,PYTHON101,2026-01-10,enrolled,,",
            "EMP0001,PYTHON101,2026-02-01,in_progress,,",
            "EMP0002,PYTHON101,2026-01-15,completed,2026-02-15,88.00",
        ),
    )

    EnrolmentImportProcessingService(factory, enrolment_settings).process(job.id)
    db_session.expire_all()

    refreshed = jobs.get_by_id(job.id)
    assert refreshed is not None
    assert refreshed.status is ImportJobStatus.COMPLETED
    assert refreshed.success_count == 2
    assert refreshed.failed_count == 1
    assert CourseEnrolmentRepository(db_session).count() == 2

    errors = ImportErrorRepository(db_session).list_for_job(job.id, offset=0, limit=10)
    assert len(errors) == 1
    assert errors[0].code == ImportErrorCode.DUPLICATE_IN_FILE.value
    assert errors[0].record_key == "EMP0001|PYTHON101"
    assert errors[0].employee_number == "EMP0001"
    assert employee.id is not None and course.id is not None


def test_unknown_employee_and_course(
    db_session: Session,
    sample_department,
    enrolment_settings: Settings,
) -> None:
    _seed_employee_and_course(db_session, sample_department)
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()
    _write_job_csv(
        enrolment_settings,
        job.id,
        _enrolment_csv(
            "MISSING,PYTHON101,2026-01-10,enrolled,,",
            "EMP0001,MISSING,2026-01-10,enrolled,,",
        ),
    )

    before = CourseEnrolmentRepository(db_session).count()
    EnrolmentImportProcessingService(factory, enrolment_settings).process(job.id)
    assert CourseEnrolmentRepository(db_session).count() == before

    errors = ImportErrorRepository(db_session).list_for_job(job.id, offset=0, limit=20)
    codes = {e.code for e in errors}
    assert ImportErrorCode.UNKNOWN_EMPLOYEE.value in codes
    assert ImportErrorCode.UNKNOWN_COURSE.value in codes


def test_existing_db_pair_already_exists(
    db_session: Session,
    sample_department,
    enrolment_settings: Settings,
) -> None:
    employee, course = _seed_employee_and_course(db_session, sample_department)
    db_session.add(
        CourseEnrolment(
            employee_id=employee.id,
            course_id=course.id,
            enrolment_date=date(2026, 1, 1),
            status=CourseEnrolmentStatus.ENROLLED,
        )
    )
    db_session.flush()

    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()
    _write_job_csv(
        enrolment_settings,
        job.id,
        _enrolment_csv("EMP0001,PYTHON101,2026-03-01,enrolled,,"),
    )

    before = CourseEnrolmentRepository(db_session).count()
    EnrolmentImportProcessingService(factory, enrolment_settings).process(job.id)
    assert CourseEnrolmentRepository(db_session).count() == before

    errors = ImportErrorRepository(db_session).list_for_job(job.id, offset=0, limit=5)
    assert errors[0].code == ImportErrorCode.ALREADY_EXISTS.value
    assert errors[0].record_key == "EMP0001|PYTHON101"


def test_enrolment_dry_run_zero_inserts(
    db_session: Session,
    sample_department,
    enrolment_settings: Settings,
) -> None:
    _seed_employee_and_course(db_session, sample_department)
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        dry_run=True,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()
    _write_job_csv(
        enrolment_settings,
        job.id,
        _enrolment_csv("EMP0001,PYTHON101,2026-01-10,enrolled,,"),
    )

    before = CourseEnrolmentRepository(db_session).count()
    EnrolmentImportProcessingService(factory, enrolment_settings).process(job.id)
    assert CourseEnrolmentRepository(db_session).count() == before

    refreshed = jobs.get_by_id(job.id)
    assert refreshed is not None
    assert refreshed.status is ImportJobStatus.COMPLETED
    assert refreshed.success_count == 1


def test_batch_lookup_methods_used(
    db_session: Session,
    sample_department,
    enrolment_settings: Settings,
) -> None:
    _seed_employee_and_course(db_session, sample_department)
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()
    _write_job_csv(
        enrolment_settings,
        job.id,
        _enrolment_csv("EMP0001,PYTHON101,2026-01-10,enrolled,,"),
    )

    with (
        patch.object(
            EmployeeRepository,
            "map_employee_numbers_to_ids",
            autospec=True,
            side_effect=EmployeeRepository.map_employee_numbers_to_ids,
        ) as map_emp,
        patch.object(
            CourseRepository,
            "map_course_codes_to_ids",
            autospec=True,
            side_effect=CourseRepository.map_course_codes_to_ids,
        ) as map_course,
        patch.object(
            CourseEnrolmentRepository,
            "find_existing_pairs",
            autospec=True,
            side_effect=CourseEnrolmentRepository.find_existing_pairs,
        ) as find_pairs,
        patch.object(
            CourseEnrolmentRepository,
            "add_many",
            autospec=True,
            side_effect=CourseEnrolmentRepository.add_many,
        ) as add_many,
    ):
        EnrolmentImportProcessingService(factory, enrolment_settings).process(job.id)
        assert map_emp.called
        assert map_course.called
        assert find_pairs.called
        assert add_many.called


def test_post_imports_enrolments(
    enrolment_api_client: TestClient,
    db_session: Session,
    sample_department,
) -> None:
    _seed_employee_and_course(db_session, sample_department)
    db_session.flush()
    payload = _enrolment_csv("EMP0001,PYTHON101,2026-01-10,enrolled,,").encode("utf-8")
    with patch("smarthris.api.routers.imports.process_import_job") as mocked:
        response = enrolment_api_client.post(
            "/imports/enrolments",
            files={"file": ("enrolments.csv", payload, "text/csv")},
        )
        assert response.status_code == 201, response.text
        assert response.json()["import_target"] == ImportTarget.ENROLMENTS.value
        mocked.assert_called_once()


def test_enrolment_retry_preserves_target(
    enrolment_api_client: TestClient,
    db_session: Session,
    enrolment_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    parent = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    jobs.mark_running(parent)
    jobs.update_counts(parent, total_rows=1, processed_rows=1, failed_count=1)
    jobs.mark_failed(parent, error_message="simulated")
    db_session.commit()
    _write_job_csv(
        enrolment_settings,
        parent.id,
        _enrolment_csv("EMP0001,PYTHON101,2026-01-10,enrolled,,"),
    )

    with patch("smarthris.api.routers.imports.process_import_job") as mocked:
        response = enrolment_api_client.post(f"/imports/{parent.id}/retry")
        assert response.status_code == 201, response.text
        assert response.json()["import_target"] == ImportTarget.ENROLMENTS.value
        mocked.assert_called_once()


def test_dispatch_routes_enrolments(
    db_session: Session,
    enrolment_settings: Settings,
) -> None:
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()

    with patch(
        "smarthris.services.enrolment_import_processing_service.EnrolmentImportProcessingService.process"
    ) as enrolment_process:
        process_import_job(
            job.id,
            session_factory=factory,
            settings=enrolment_settings,
        )
        enrolment_process.assert_called_once()
