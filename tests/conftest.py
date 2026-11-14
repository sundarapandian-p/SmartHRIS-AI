"""Shared pytest fixtures."""

pytest_plugins = ["tests.auth_helpers"]

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings, get_settings
from smarthris.db.base import Base
from smarthris.db.session import create_db_engine, create_session_factory, verify_database_connection
from smarthris.main import create_app
from smarthris.performance.gate import PERFORMANCE_ENV_VAR, performance_runs_enabled
from smarthris.repositories.department_repository import DepartmentRepository
from tests.auth_helpers import TEST_JWT_SECRET, attach_active_user_override
from tests.test_db_guard import assert_safe_integration_database_url


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip ``performance``-marked tests unless SMART_HRIS_RUN_PERFORMANCE=1."""
    if performance_runs_enabled():
        return
    skip_perf = pytest.mark.skip(
        reason=(
            f"Performance benchmarks are opt-in; set {PERFORMANCE_ENV_VAR}=1 "
            "with an approved MySQL test/bench database."
        )
    )
    for item in items:
        if item.get_closest_marker("performance") is not None:
            item.add_marker(skip_perf)


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
def api_client(
    db_session: Session,
    integration_database_url: str,
    hr_test_user,
) -> TestClient:
    """API client using the integration DB session (employee tests)."""
    settings = Settings(
        _env_file=None,
        app_name="SmartHRIS-Integration",
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
    attach_active_user_override(app, hr_test_user)
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


@pytest.fixture(autouse=True)
def _streamlit_page_guard_test_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable Streamlit page-guard test mode only for test_streamlit_* modules."""
    nodeid = request.node.nodeid.replace("\\", "/")
    if "/test_streamlit" not in nodeid:
        return
    from streamlit_app.constants import ENV_PAGE_GUARD_TEST

    monkeypatch.setenv(ENV_PAGE_GUARD_TEST, "1")
    monkeypatch.setenv("APP_ENV", "development")


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
