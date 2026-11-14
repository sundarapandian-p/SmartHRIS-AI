"""Phase 6.2 — RBAC and API protection tests."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_current_active_user, get_db
from smarthris.config import Settings
from smarthris.main import create_app
from smarthris.models.hr_document import HrDocumentVisibility
from smarthris.models.user import User, UserRole
from tests.auth_helpers import (
    TEST_JWT_SECRET,
    attach_active_user_override,
    bearer_headers,
    create_test_user,
)
from smarthris.models.employee import Employee, EmployeeStatus

pytest.importorskip("bcrypt")
pytest.importorskip("jwt")


def _app(
    db_session: Session,
    integration_database_url: str,
    user: User,
    **settings_kw,
) -> FastAPI:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key=TEST_JWT_SECRET,
        **settings_kw,
    )
    app = create_app(settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, user)
    return app


def test_unauthenticated_employee_list_returns_401(
    db_session: Session,
    integration_database_url: str,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key=TEST_JWT_SECRET,
    )
    app = create_app(settings)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        response = client.get("/employees")
    assert response.status_code == 401


def test_invalid_token_returns_401(
    db_session: Session,
    integration_database_url: str,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key=TEST_JWT_SECRET,
    )
    app = create_app(settings)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        response = client.get(
            "/employees",
            headers={"Authorization": "Bearer not-a-valid-jwt"},
        )
    assert response.status_code == 401


def test_inactive_user_returns_403(
    db_session: Session,
    integration_database_url: str,
) -> None:
    user = create_test_user(db_session, role=UserRole.HR, is_active=False)
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key=TEST_JWT_SECRET,
    )
    app = create_app(settings)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        response = client.get("/employees", headers=bearer_headers(user))
    assert response.status_code == 403
    app.dependency_overrides.clear()


def test_employee_cannot_list_employees(
    db_session: Session,
    integration_database_url: str,
    employee_test_user: User,
) -> None:
    app = _app(db_session, integration_database_url, employee_test_user)
    with TestClient(app) as client:
        response = client.get("/employees")
    assert response.status_code == 403


def test_employee_can_read_own_record_only(
    db_session: Session,
    integration_database_url: str,
    employee_test_user: User,
    sample_department,
) -> None:
    app = _app(db_session, integration_database_url, employee_test_user)
    with TestClient(app) as client:
        own = client.get(f"/employees/{employee_test_user.employee_id}")
        assert own.status_code == 200

        other = Employee(
            employee_number="E-OTHER",
            first_name="Other",
            last_name="Person",
            work_email=f"other.{uuid.uuid4().hex[:6]}@example.com",
            department_id=sample_department.id,
            status=EmployeeStatus.ACTIVE,
        )
        db_session.add(other)
        db_session.flush()
        foreign = client.get(f"/employees/{other.id}")
        assert foreign.status_code == 404


def test_employee_denied_import_upload(
    db_session: Session,
    integration_database_url: str,
    employee_test_user: User,
    tmp_path,
) -> None:
    app = _app(
        db_session,
        integration_database_url,
        employee_test_user,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=50_000,
    )
    with patch("smarthris.api.routers.imports.process_import_job"):
        with TestClient(app) as client:
            response = client.post(
                "/imports",
                files={
                    "file": (
                        "employees.csv",
                        b"Employee Number,First Name,Last Name,Work Email,Department ID\n",
                        "text/csv",
                    )
                },
            )
    assert response.status_code == 403


def test_employee_denied_lms_analytics(
    db_session: Session,
    integration_database_url: str,
    employee_test_user: User,
) -> None:
    app = _app(db_session, integration_database_url, employee_test_user)
    with TestClient(app) as client:
        response = client.get("/analytics/lms/summary")
    assert response.status_code == 403


def test_employee_cannot_upload_documents(
    db_session: Session,
    integration_database_url: str,
    employee_test_user: User,
    tmp_path,
) -> None:
    app = _app(
        db_session,
        integration_database_url,
        employee_test_user,
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=10_000,
    )
    with patch("smarthris.api.routers.documents.process_hr_document"):
        with TestClient(app) as client:
            response = client.post(
                "/documents",
                files={"file": ("note.txt", b"hello", "text/plain")},
            )
    assert response.status_code == 403


def test_hr_can_upload_document_defaults_hr_only_visibility(
    db_session: Session,
    integration_database_url: str,
    hr_test_user: User,
    tmp_path,
) -> None:
    app = _app(
        db_session,
        integration_database_url,
        hr_test_user,
        rag_document_dir=str(tmp_path / "documents"),
        max_upload_bytes=10_000,
    )
    with patch("smarthris.api.routers.documents.process_hr_document"):
        with TestClient(app) as client:
            response = client.post(
                "/documents",
                files={"file": ("note.txt", b"policy body", "text/plain")},
            )
    assert response.status_code == 201, response.text
    assert response.json()["visibility"] == HrDocumentVisibility.HR_ONLY.value