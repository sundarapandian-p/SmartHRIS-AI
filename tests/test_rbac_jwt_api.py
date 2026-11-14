"""RBAC API tests using real Bearer JWT (no get_current_active_user override)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.models.hr_document import HrDocumentVisibility
from smarthris.models.user import User, UserRole
from smarthris.repositories.user_repository import UserRepository
from tests.auth_helpers import (
    TEST_JWT_SECRET,
    bearer_headers,
    create_test_user,
    make_jwt_test_client,
)


@pytest.fixture
def jwt_client(db_session: Session, integration_database_url: str):
    client = make_jwt_test_client(db_session, integration_database_url)
    with client:
        yield client


def test_jwt_employee_reads_own_record(
    jwt_client,
    db_session: Session,
    employee_test_user: User,
) -> None:
    headers = bearer_headers(employee_test_user)
    response = jwt_client.get(
        f"/employees/{employee_test_user.employee_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text


def test_jwt_employee_cannot_read_other_employee(
    jwt_client,
    db_session: Session,
    employee_test_user: User,
    sample_department,
) -> None:
    other = Employee(
        employee_number=f"E-OTHER-{uuid.uuid4().hex[:6]}",
        first_name="Other",
        last_name="User",
        work_email=f"other.{uuid.uuid4().hex[:6]}@example.com",
        department_id=sample_department.id,
        status=EmployeeStatus.ACTIVE,
    )
    db_session.add(other)
    db_session.flush()
    headers = bearer_headers(employee_test_user)
    response = jwt_client.get(f"/employees/{other.id}", headers=headers)
    assert response.status_code == 404


def test_jwt_employee_denied_employees_list(jwt_client, employee_test_user: User) -> None:
    response = jwt_client.get("/employees", headers=bearer_headers(employee_test_user))
    assert response.status_code == 403


def test_jwt_employee_denied_imports(jwt_client, employee_test_user: User) -> None:
    response = jwt_client.post(
        "/imports",
        headers=bearer_headers(employee_test_user),
        files={"file": ("e.csv", b"h\n", "text/csv")},
    )
    assert response.status_code == 403


def test_jwt_employee_denied_lms_summary(jwt_client, employee_test_user: User) -> None:
    response = jwt_client.get(
        "/analytics/lms/summary",
        headers=bearer_headers(employee_test_user),
    )
    assert response.status_code == 403


def test_jwt_employee_document_search_allowed_empty_index(
    jwt_client,
    employee_test_user: User,
) -> None:
    response = jwt_client.post(
        "/documents/search",
        headers=bearer_headers(employee_test_user),
        json={"query": "leave policy"},
    )
    assert response.status_code == 200, response.text


def test_jwt_employee_rag_query_allowed_empty_index(
    jwt_client,
    employee_test_user: User,
) -> None:
    response = jwt_client.post(
        "/rag/query",
        headers=bearer_headers(employee_test_user),
        json={"question": "What is the leave policy?"},
    )
    assert response.status_code == 200, response.text


def test_jwt_hr_document_upload_default_visibility(
    db_session: Session,
    integration_database_url: str,
    hr_test_user: User,
    tmp_path,
) -> None:
    from unittest.mock import patch

    from smarthris.api.deps import get_db
    from smarthris.config import Settings
    from smarthris.main import create_app
    from fastapi.testclient import TestClient

    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key=TEST_JWT_SECRET,
        rag_document_dir=str(tmp_path / "docs"),
        max_upload_bytes=50_000,
    )
    app = create_app(settings)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with patch("smarthris.api.routers.documents.process_hr_document"):
        with TestClient(app) as client:
            response = client.post(
                "/documents",
                headers=bearer_headers(hr_test_user),
                files={"file": ("note.txt", b"hello staff", "text/plain")},
            )
    assert response.status_code == 201, response.text
    assert response.json()["visibility"] == HrDocumentVisibility.HR_ONLY.value