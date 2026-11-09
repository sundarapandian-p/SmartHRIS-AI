"""Phase 5.3.2C2B — course CSV import tests."""

from __future__ import annotations

from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.importing.course_column_mapping import map_course_headers, resolve_canonical_header
from smarthris.importing.parsers.course_csv_parser import parse_course_csv
from smarthris.importing.storage import import_storage_path
from smarthris.importing.validation.course_row_schema import parse_course_import_row
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.main import create_app
from smarthris.models.course import Course, CourseStatus
from smarthris.models.import_error import ImportError
from smarthris.models.import_job import ImportFileType, ImportJobStatus, ImportTarget
from smarthris.repositories.course_repository import CourseRepository
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.services.course_import_processing_service import CourseImportProcessingService
from smarthris.services.import_processing_service import process_import_job


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
def course_import_settings(tmp_path: Path, integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Course-Import",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=100_000,
        import_batch_size=50,
    )


@pytest.fixture
def course_api_client(
    db_session: Session,
    course_import_settings: Settings,
) -> TestClient:
    app = create_app(course_import_settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _course_csv(*rows: str, header: str | None = None) -> str:
    hdr = header or "course_code,course_name,category,description,duration_hours,status"
    return hdr + "\n" + "\n".join(rows) + "\n"


def _write_job_csv(settings: Settings, job_id: str, body: str) -> Path:
    path = import_storage_path(settings.import_temp_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_course_header_aliases() -> None:
    assert resolve_canonical_header("Course Code") == "course_code"
    assert resolve_canonical_header("Course ID") == "course_code"
    assert resolve_canonical_header("Title") == "course_name"
    assert resolve_canonical_header("Duration Hours") == "duration_hours"
    mapping = map_course_headers(
        ["Course Code", "Course Name", "Category", "Duration", "Status"]
    )
    assert mapping.is_valid
    assert "course_code" in mapping.column_to_original
    assert "duration_hours" in mapping.column_to_original


def test_course_headers_missing_required() -> None:
    mapping = map_course_headers(["course_code", "course_name"])
    assert not mapping.is_valid
    fields = {issue.field for issue in mapping.issues}
    assert "category" in fields
    assert "duration_hours" in fields


def test_parse_course_row_required_and_defaults() -> None:
    row, issues = parse_course_import_row(
        2,
        {
            "course_code": "SAFE-101",
            "course_name": "Safety",
            "category": "Compliance",
            "description": "",
            "duration_hours": "4.5",
            "status": "",
        },
    )
    assert not issues
    assert row is not None
    assert row.status is CourseStatus.ACTIVE
    assert row.duration_hours == Decimal("4.5")
    assert row.description is None


def test_parse_course_row_invalid_duration_and_status() -> None:
    _, issues = parse_course_import_row(
        2,
        {
            "course_code": "SAFE-102",
            "course_name": "Safety",
            "category": "Compliance",
            "duration_hours": "0",
            "status": "active",
        },
    )
    assert any(i.code == ImportErrorCode.INVALID_VALUE.value for i in issues)

    _, status_issues = parse_course_import_row(
        3,
        {
            "course_code": "SAFE-103",
            "course_name": "Safety",
            "category": "Compliance",
            "duration_hours": "1.00",
            "status": "archived",
        },
    )
    assert any(i.code == ImportErrorCode.INVALID_STATUS.value for i in status_issues)


def test_parse_course_row_duration_precision_limit() -> None:
    _, issues = parse_course_import_row(
        2,
        {
            "course_code": "SAFE-104",
            "course_name": "Safety",
            "category": "Compliance",
            "duration_hours": "12345.678",
            "status": "active",
        },
    )
    assert issues
    assert any(i.field == "duration_hours" for i in issues)


def test_course_csv_parser_streams_aliases() -> None:
    body = _course_csv(
        "C1,Intro,Compliance,,2.00,active",
        header="Course Code,Course Name,Category,Description,Duration Hours,Status",
    )
    rows = list(parse_course_csv(StringIO(body)))
    assert len(rows) == 1
    assert rows[0].values["course_code"] == "C1"
    assert rows[0].values["duration_hours"] == "2.00"


def test_course_import_inserts_and_reports_duplicates(
    db_session: Session,
    course_import_settings: Settings,
) -> None:
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="courses.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.COURSES,
    )
    db_session.flush()
    _write_job_csv(
        course_import_settings,
        job.id,
        _course_csv(
            "SAFE-101,Safety,Compliance,Intro,4.50,active",
            "SAFE-101,Dup,Compliance,,1.00,active",
            "SAFE-102,Fire,Compliance,,3.00,inactive",
        ),
    )

    CourseImportProcessingService(factory, course_import_settings).process(job.id)

    db_session.expire_all()
    refreshed = jobs.get_by_id(job.id)
    assert refreshed is not None
    assert refreshed.status is ImportJobStatus.COMPLETED
    assert refreshed.success_count == 2
    assert refreshed.failed_count == 1
    assert refreshed.import_target is ImportTarget.COURSES

    codes = set(db_session.scalars(select(Course.course_code)).all())
    assert codes >= {"SAFE-101", "SAFE-102"}

    errors = ImportErrorRepository(db_session).list_for_job(job.id, offset=0, limit=20)
    assert len(errors) == 1
    assert errors[0].code == ImportErrorCode.DUPLICATE_IN_FILE.value
    assert errors[0].record_key == "SAFE-101"
    assert errors[0].employee_number is None


def test_course_import_already_exists_in_db(
    db_session: Session,
    course_import_settings: Settings,
) -> None:
    db_session.add(
        Course(
            course_code="EXIST-1",
            course_name="Existing",
            category="Compliance",
            duration_hours=Decimal("1.00"),
            status=CourseStatus.ACTIVE,
        )
    )
    db_session.flush()

    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="courses.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.COURSES,
    )
    db_session.flush()
    _write_job_csv(
        course_import_settings,
        job.id,
        _course_csv("EXIST-1,New Name,Compliance,,2.00,active"),
    )

    before = CourseRepository(db_session).count()
    CourseImportProcessingService(factory, course_import_settings).process(job.id)
    after = CourseRepository(db_session).count()
    assert after == before

    errors = ImportErrorRepository(db_session).list_for_job(job.id, offset=0, limit=10)
    assert errors[0].code == ImportErrorCode.ALREADY_EXISTS.value
    assert errors[0].record_key == "EXIST-1"


def test_course_import_dry_run_zero_inserts(
    db_session: Session,
    course_import_settings: Settings,
) -> None:
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="courses.csv",
        file_type=ImportFileType.CSV,
        dry_run=True,
        import_target=ImportTarget.COURSES,
    )
    db_session.flush()
    _write_job_csv(
        course_import_settings,
        job.id,
        _course_csv("DRY-1,Dry Course,Compliance,,1.50,active"),
    )

    before = CourseRepository(db_session).count()
    CourseImportProcessingService(factory, course_import_settings).process(job.id)
    assert CourseRepository(db_session).count() == before

    refreshed = jobs.get_by_id(job.id)
    assert refreshed is not None
    assert refreshed.status is ImportJobStatus.COMPLETED
    assert refreshed.success_count == 1
    assert refreshed.failed_count == 0


def test_post_imports_courses_and_status(
    course_api_client: TestClient,
    db_session: Session,
) -> None:
    payload = _course_csv("API-1,API Course,Compliance,,2.00,active").encode("utf-8")
    response = course_api_client.post(
        "/imports/courses",
        files={"file": ("courses.csv", payload, "text/csv")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["import_target"] == ImportTarget.COURSES.value
    assert body["status"] in {
        ImportJobStatus.PENDING.value,
        ImportJobStatus.RUNNING.value,
        ImportJobStatus.COMPLETED.value,
    }

    status = course_api_client.get(f"/imports/{body['id']}")
    assert status.status_code == 200
    assert status.json()["import_target"] == ImportTarget.COURSES.value


def test_course_retry_preserves_target(
    course_api_client: TestClient,
    db_session: Session,
    course_import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    parent = jobs.create(
        filename="courses.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.COURSES,
    )
    jobs.mark_running(parent)
    jobs.update_counts(parent, total_rows=1, processed_rows=1, failed_count=1)
    jobs.mark_failed(parent, error_message="simulated failure")
    db_session.commit()
    _write_job_csv(
        course_import_settings,
        parent.id,
        _course_csv("RETRY-1,Retry Course,Compliance,,1.00,active"),
    )

    with patch(
        "smarthris.api.routers.imports.process_import_job",
    ) as mocked:
        response = course_api_client.post(f"/imports/{parent.id}/retry")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["import_target"] == ImportTarget.COURSES.value
        assert body["parent_job_id"] == parent.id
        mocked.assert_called_once()


def test_dispatch_employees_courses_enrolments(
    db_session: Session,
    course_import_settings: Settings,
) -> None:
    factory = _TrackingSessionFactory(db_session)
    jobs = ImportJobRepository(db_session)

    employee_job = jobs.create(
        filename="employees.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.EMPLOYEES,
    )
    course_job = jobs.create(
        filename="courses.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.COURSES,
    )
    enrolment_job = jobs.create(
        filename="enrolments.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.ENROLMENTS,
    )
    db_session.flush()
    _write_job_csv(
        course_import_settings,
        course_job.id,
        _course_csv("DISP-1,Dispatch,Compliance,,1.00,active"),
    )

    with (
        patch(
            "smarthris.services.import_processing_service.ImportProcessingService.process"
        ) as emp_process,
        patch(
            "smarthris.services.course_import_processing_service.CourseImportProcessingService.process"
        ) as course_process,
        patch(
            "smarthris.services.enrolment_import_processing_service.EnrolmentImportProcessingService.process"
        ) as enrolment_process,
    ):
        process_import_job(
            employee_job.id,
            session_factory=factory,
            settings=course_import_settings,
        )
        emp_process.assert_called_once()
        course_process.assert_not_called()
        enrolment_process.assert_not_called()

        emp_process.reset_mock()
        process_import_job(
            course_job.id,
            session_factory=factory,
            settings=course_import_settings,
        )
        course_process.assert_called_once()
        emp_process.assert_not_called()
        enrolment_process.assert_not_called()

        course_process.reset_mock()
        process_import_job(
            enrolment_job.id,
            session_factory=factory,
            settings=course_import_settings,
        )
        enrolment_process.assert_called_once()
        emp_process.assert_not_called()
        course_process.assert_not_called()
