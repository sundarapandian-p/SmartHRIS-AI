"""Shared authentication helpers for API integration tests."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from smarthris.api.deps import get_current_active_user, get_db
from smarthris.core.email import normalize_email
from smarthris.core.security import create_access_token, hash_password
from smarthris.models.user import User, UserRole
from smarthris.repositories.user_repository import UserRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

TEST_JWT_SECRET = "pytest-auth-secret-key-at-least-32-chars"


def create_test_user(
    db_session: Session,
    *,
    role: UserRole = UserRole.HR,
    email: str | None = None,
    password: str = "SecretPass1!",
    employee_id: int | None = None,
    is_active: bool = True,
) -> User:
    suffix = uuid.uuid4().hex[:8]
    resolved_email = normalize_email(email or f"test-{role.value}-{suffix}@example.com")
    user = User(
        email=resolved_email,
        password_hash=hash_password(password),
        role=role,
        employee_id=employee_id,
        is_active=is_active,
    )
    return UserRepository(db_session).add(user)


def attach_active_user_override(app: FastAPI, user: User) -> None:
    """Pin API tests to a specific active user (no global auth bypass)."""

    def _active_user() -> User:
        return user

    app.dependency_overrides[get_current_active_user] = _active_user


def make_jwt_test_client(
    db_session: Session,
    integration_database_url: str,
) -> TestClient:
    """API TestClient with JWT auth only (no get_current_active_user override)."""
    from smarthris.config import Settings
    from smarthris.main import create_app

    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
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
    return TestClient(app)


def bearer_headers(user: User, *, settings_secret: str = TEST_JWT_SECRET) -> dict[str, str]:
    from smarthris.config import Settings

    settings = Settings(_env_file=None, jwt_secret_key=settings_secret)
    token, _expires = create_access_token(user_id=user.id, settings=settings)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def hr_test_user(db_session: Session) -> User:
    return create_test_user(db_session, role=UserRole.HR)


@pytest.fixture
def admin_test_user(db_session: Session) -> User:
    return create_test_user(db_session, role=UserRole.ADMIN)


@pytest.fixture
def employee_test_user(db_session: Session, sample_department) -> User:
    from smarthris.models.employee import Employee, EmployeeStatus

    suffix = uuid.uuid4().hex[:8]
    employee = Employee(
        employee_number=f"E-{suffix}",
        first_name="Self",
        last_name="Service",
        work_email=f"self.{suffix}@example.com",
        department_id=sample_department.id,
        status=EmployeeStatus.ACTIVE,
    )
    db_session.add(employee)
    db_session.flush()
    return create_test_user(
        db_session,
        role=UserRole.EMPLOYEE,
        employee_id=employee.id,
    )