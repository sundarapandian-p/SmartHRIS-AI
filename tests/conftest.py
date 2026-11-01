"""Shared pytest fixtures."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings, get_settings
from smarthris.db.base import Base
from smarthris.db.session import create_db_engine, create_session_factory, verify_database_connection
from smarthris.main import create_app
from smarthris.repositories.department_repository import DepartmentRepository
from tests.test_db_guard import assert_safe_integration_database_url


@pytest.fixture
def test_settings() -> Settings:
    """Isolated settings for unit tests (no .env required)."""
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Test",
        app_env="development",
        debug=True,
        database_url="mysql+pymysql://test_user:test_pass@127.0.0.1:3306/smarthris_test",
    )


@pytest.fixture
def client(test_settings: Settings) -> TestClient:
    """HTTP client with lifespan so app.state.db_engine exists."""
    get_settings.cache_clear()
    app = create_app(test_settings)
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def integration_database_url() -> str:
    """Test database URL from .env (TEST_DATABASE_URL) or derived smarthris_test."""
    get_settings.cache_clear()
    settings = get_settings()
    url = settings.sqlalchemy_test_database_url
    get_settings.cache_clear()
    return assert_safe_integration_database_url(url)


@pytest.fixture(scope="session")
def integration_engine(integration_database_url: str):
    """Engine pointed at the integration database; skips if MySQL is unavailable."""
    engine = create_db_engine(
        Settings(_env_file=None, database_url=integration_database_url),
    )
    if not verify_database_connection(engine):
        pytest.skip("Integration database is not available (check TEST_DATABASE_URL / MySQL)")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def db_session(integration_engine) -> Session:
    """Transactional session rolled back after each test (savepoints survive service commit)."""
    connection = integration_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def api_client(db_session: Session, integration_database_url: str) -> TestClient:
    """API client using the integration DB session (employee tests)."""
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Integration",
        database_url=integration_database_url,
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


@pytest.fixture
def sample_department(db_session: Session):
    """Persist a department for employee tests."""
    repo = DepartmentRepository(db_session)
    department = repo.create("Engineering", description="Product engineering")
    db_session.flush()
    return department


def post_employee(client: TestClient, department_id: int, **fields) -> dict:
    """Create an employee via API and return the JSON body."""
    payload = {
        "employee_number": fields.pop("employee_number", "E-DEFAULT"),
        "first_name": fields.pop("first_name", "Test"),
        "last_name": fields.pop("last_name", "User"),
        "work_email": fields.pop("work_email", "test.user@example.com"),
        "department_id": department_id,
        **fields,
    }
    response = client.post("/employees", json=payload)
    assert response.status_code == 201, response.text
    return response.json()
