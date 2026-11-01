"""Phase 3.3 — CSV upload API and import job creation."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.core.exceptions import PayloadTooLargeError, ValidationError
from smarthris.importing.storage import (
    CHUNK_SIZE,
    import_storage_path,
    sanitize_original_filename,
    save_upload_stream,
)
from smarthris.main import create_app
from smarthris.models.import_job import ImportJobStatus
from smarthris.repositories.import_job_repository import ImportJobRepository


@pytest.fixture
def import_api_client(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
) -> TestClient:
    """API client with isolated import temp dir under pytest tmp_path."""
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-Upload",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=1024,
    )
    app = create_app(settings)

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


def _csv_bytes(rows: int = 1) -> bytes:
    lines = ["Employee Number,First Name,Last Name,Work Email,Department ID"]
    for i in range(rows):
        lines.append(f"E{i},Ada,Lovelace,ada{i}@example.com,1")
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_sanitize_original_filename_strips_paths() -> None:
    assert sanitize_original_filename(r"..\..\Windows\evil.csv") == "evil.csv"
    assert sanitize_original_filename("/tmp/nested/employees.CSV") == "employees.CSV"
    with pytest.raises(ValidationError):
        sanitize_original_filename("")
    with pytest.raises(ValidationError):
        sanitize_original_filename(None)


def test_save_upload_stream_chunked_and_size_limit(tmp_path: Path) -> None:
    destination = tmp_path / "job.csv"
    source = io.BytesIO(b"a" * 100)
    written = save_upload_stream(
        source=source,
        destination=destination,
        max_upload_bytes=1000,
        chunk_size=16,
    )
    assert written == 100
    assert destination.read_bytes() == b"a" * 100

    oversized = tmp_path / "big.csv"
    with pytest.raises(PayloadTooLargeError):
        save_upload_stream(
            source=io.BytesIO(b"b" * 50),
            destination=oversized,
            max_upload_bytes=20,
            chunk_size=8,
        )
    assert not oversized.exists()


def test_save_upload_stream_rejects_zip_magic(tmp_path: Path) -> None:
    destination = tmp_path / "fake.csv"
    with pytest.raises(ValidationError):
        save_upload_stream(
            source=io.BytesIO(b"PK\x03\x04not-really-csv"),
            destination=destination,
            max_upload_bytes=1000,
        )
    assert not destination.exists()


def test_save_upload_stream_does_not_read_entire_file_at_once(tmp_path: Path) -> None:
    class ChunkProbe(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.max_requested = 0

        def read(self, size: int | None = -1) -> bytes:
            if size is None or size < 0:
                raise AssertionError("unbounded read is not allowed")
            self.max_requested = max(self.max_requested, size)
            return super().read(size)

    probe = ChunkProbe(b"x" * 10_000)
    destination = tmp_path / "probe.csv"
    save_upload_stream(
        source=probe,
        destination=destination,
        max_upload_bytes=50_000,
        chunk_size=CHUNK_SIZE,
    )
    assert probe.max_requested == CHUNK_SIZE


def test_upload_csv_creates_pending_job(
    import_api_client: TestClient,
    db_session: Session,
    tmp_path: Path,
) -> None:
    response = import_api_client.post(
        "/imports",
        files={"file": ("employees.csv", _csv_bytes(), "text/csv")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == ImportJobStatus.PENDING.value
    assert body["filename"] == "employees.csv"
    assert body["file_type"] == "csv"
    assert body["dry_run"] is False
    assert body["total_rows"] == 0
    assert body["processed_rows"] == 0
    assert body["success_count"] == 0
    assert body["failed_count"] == 0
    assert body["error_message"] is None
    assert "path" not in body

    job = ImportJobRepository(db_session).get_by_id(body["id"])
    assert job is not None
    assert job.status == ImportJobStatus.PENDING

    stored = import_storage_path(tmp_path / "imports", body["id"])
    assert stored.is_file()
    assert stored.name == f"{body['id']}.csv"


def test_upload_dry_run_true(import_api_client: TestClient) -> None:
    response = import_api_client.post(
        "/imports",
        data={"dry_run": "true"},
        files={"file": ("batch.csv", _csv_bytes(), "text/csv")},
    )
    assert response.status_code == 201
    assert response.json()["dry_run"] is True


def test_upload_rejects_xlsx(import_api_client: TestClient) -> None:
    response = import_api_client.post(
        "/imports",
        files={"file": ("people.xlsx", b"PK\x03\x04fake", "application/vnd.ms-excel")},
    )
    assert response.status_code == 400


def test_upload_rejects_other_extension(import_api_client: TestClient) -> None:
    response = import_api_client.post(
        "/imports",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_rejects_empty_file(import_api_client: TestClient) -> None:
    response = import_api_client.post(
        "/imports",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert response.status_code == 400


def test_upload_rejects_missing_filename(import_api_client: TestClient) -> None:
    response = import_api_client.post(
        "/imports",
        files={"file": ("", b"a,b\n1,2\n", "text/csv")},
    )
    # Starlette/FastAPI may reject before our handler; accept 400 or 422.
    assert response.status_code in {400, 422}


def test_upload_oversized_returns_413(import_api_client: TestClient) -> None:
    # Fixture max_upload_bytes=1024
    payload = b"x" * 2048
    response = import_api_client.post(
        "/imports",
        files={"file": ("big.csv", payload, "text/csv")},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


def test_path_traversal_does_not_escape_temp_dir(
    import_api_client: TestClient,
    tmp_path: Path,
) -> None:
    response = import_api_client.post(
        "/imports",
        files={"file": ("../../etc/passwd.csv", _csv_bytes(), "text/csv")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "passwd.csv"
    stored = import_storage_path(tmp_path / "imports", body["id"])
    assert stored.is_file()
    assert stored.parent == (tmp_path / "imports").resolve()
    assert not (tmp_path / "passwd.csv").exists()


def test_db_failure_removes_stored_file_and_rolls_back(
    db_session: Session,
    integration_database_url: str,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=10_000,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, raise_server_exceptions=False) as client:
        with patch(
            "smarthris.services.import_upload_service.ImportJobRepository.create",
            side_effect=RuntimeError("db boom"),
        ):
            response = client.post(
                "/imports",
                files={"file": ("employees.csv", _csv_bytes(), "text/csv")},
            )
        assert response.status_code == 500
        imports_dir = tmp_path / "imports"
        leftover = list(imports_dir.glob("*.csv")) if imports_dir.exists() else []
        assert leftover == []

    app.dependency_overrides.clear()


def test_upload_does_not_call_parser_or_validator(import_api_client: TestClient) -> None:
    with (
        patch("smarthris.importing.parsers.csv_parser.parse_csv") as parse_csv,
        patch(
            "smarthris.importing.validation.row_validator.ImportRowValidator.validate"
        ) as validate,
    ):
        response = import_api_client.post(
            "/imports",
            files={"file": ("employees.csv", _csv_bytes(), "text/csv")},
        )
    assert response.status_code == 201
    parse_csv.assert_not_called()
    validate.assert_not_called()


def test_employee_api_still_works(
    import_api_client: TestClient,
    sample_department,
) -> None:
    response = import_api_client.post(
        "/employees",
        json={
            "employee_number": "E-UPLOAD-SMOKE",
            "first_name": "Smoke",
            "last_name": "Test",
            "work_email": "smoke.upload@example.com",
            "department_id": sample_department.id,
        },
    )
    assert response.status_code == 201
