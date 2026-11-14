"""Authentication API and login flow (smarthris_test only)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

pytest.importorskip("bcrypt")
pytest.importorskip("jwt")

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.core.email import normalize_email
from smarthris.core.security import create_access_token, hash_password
from smarthris.main import create_app
from smarthris.models.user import User, UserRole
from smarthris.repositories.user_repository import UserRepository

from tests.test_db_guard import assert_safe_integration_database_url

_TEST_SECRET = "pytest-auth-secret-key-at-least-32-chars"


@pytest.fixture(autouse=True)
def _enforce_integration_database_guard(integration_database_url: str) -> None:
    assert_safe_integration_database_url(integration_database_url)


def _auth_settings(integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key=_TEST_SECRET,
        jwt_access_token_expire_minutes=60,
    )


@pytest.fixture
def auth_client(
    db_session: Session,
    integration_database_url: str,
) -> TestClient:
    settings = _auth_settings(integration_database_url)
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


def _create_user(
    db_session: Session,
    *,
    email: str,
    password: str,
    role: UserRole = UserRole.HR,
    is_active: bool = True,
    employee_id: int | None = None,
) -> User:
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
        role=role,
        employee_id=employee_id,
        is_active=is_active,
    )
    return UserRepository(db_session).add(user)


def test_login_success_updates_last_login(auth_client: TestClient, db_session: Session) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"hr-user-{suffix}@example.com"
    _create_user(db_session, email=email, password="SecretPass1!")

    response = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "SecretPass1!"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 60 * 60
    assert "access_token" in body
    assert "password_hash" not in body

    user = UserRepository(db_session).get_by_email(email)
    assert user is not None
    assert user.last_login_at is not None


def test_login_invalid_credentials(auth_client: TestClient, db_session: Session) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"unknown-{suffix}@example.com"
    _create_user(db_session, email=email, password="SecretPass1!")

    response = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert response.json()["detail"] == "Invalid email or password"

    missing = auth_client.post(
        "/auth/login",
        json={"email": f"nobody-{suffix}@example.com", "password": "SecretPass1!"},
    )
    assert missing.status_code == 401
    assert missing.json()["detail"] == "Invalid email or password"


def test_login_inactive_user(auth_client: TestClient, db_session: Session) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"inactive-{suffix}@example.com"
    _create_user(db_session, email=email, password="SecretPass1!", is_active=False)

    response = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "SecretPass1!"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_auth_me_with_valid_token(auth_client: TestClient, db_session: Session) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"me-user-{suffix}@example.com"
    _create_user(db_session, email=email, password="SecretPass1!", role=UserRole.ADMIN)

    login = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "SecretPass1!"},
    )
    token = login.json()["access_token"]

    response = auth_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == normalize_email(email)
    assert body["role"] == "admin"
    assert body["is_active"] is True
    assert "password_hash" not in body


def test_auth_me_without_token(auth_client: TestClient) -> None:
    response = auth_client.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


def test_auth_me_invalid_token(auth_client: TestClient) -> None:
    response = auth_client.get(
        "/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"

    jwt = pytest.importorskip("jwt")
    forged = jwt.encode(
        {"sub": "99999", "iat": 1, "exp": 9_999_999_999},
        "wrong-signing-key-32-characters-min",
        algorithm="HS256",
    )
    bad_sig = auth_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {forged}"},
    )
    assert bad_sig.status_code == 401


def test_login_rejects_password_over_72_bytes(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"long-pass-{suffix}@example.com"
    _create_user(db_session, email=email, password="SecretPass1!")

    response = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "a" * 73},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_login_returns_503_when_jwt_secret_missing(
    db_session: Session,
    integration_database_url: str,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=integration_database_url,
        jwt_secret_key="",
    )
    app = create_app(settings)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    suffix = uuid.uuid4().hex[:8]
    email = f"jwt-misconfig-{suffix}@example.com"
    _create_user(db_session, email=email, password="SecretPass1!")

    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"email": email, "password": "SecretPass1!"},
        )
    app.dependency_overrides.clear()

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "jwt_misconfigured"
    assert "Invalid email or password" not in body["detail"]


def test_auth_me_expired_token(auth_client: TestClient) -> None:
    jwt = pytest.importorskip("jwt")
    from datetime import UTC, datetime, timedelta

    past = datetime.now(tz=UTC) - timedelta(minutes=1)
    token = jwt.encode(
        {
            "sub": "1",
            "iat": int(past.timestamp()) - 120,
            "exp": int(past.timestamp()),
        },
        _TEST_SECRET,
        algorithm="HS256",
    )
    response = auth_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


def test_auth_me_inactive_user_returns_403(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"inactive-me-{suffix}@example.com"
    user = _create_user(db_session, email=email, password="SecretPass1!")
    token, _ = create_access_token(
        user_id=user.id,
        settings=Settings(
            _env_file=None,
            jwt_secret_key=_TEST_SECRET,
            jwt_access_token_expire_minutes=60,
        ),
    )
    user.is_active = False
    db_session.flush()

    response = auth_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_auth_me_deleted_user_returns_401(
    auth_client: TestClient,
    db_session: Session,
) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"deleted-{suffix}@example.com"
    user = _create_user(db_session, email=email, password="SecretPass1!")
    token, _ = create_access_token(
        user_id=user.id,
        settings=Settings(_env_file=None, jwt_secret_key=_TEST_SECRET),
    )
    db_session.delete(user)
    db_session.flush()

    response = auth_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"