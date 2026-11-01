"""Guardrails for integration tests that touch real MySQL databases."""

from urllib.parse import urlparse

import pytest
from _pytest.outcomes import Failed

APPROVED_INTEGRATION_DATABASE_NAMES: frozenset[str] = frozenset({"smarthris_test"})


def assert_safe_integration_database_url(database_url: str | None) -> str:
    if not database_url or not str(database_url).strip():
        pytest.fail(
            "Integration database URL is missing. Set TEST_DATABASE_URL or use "
            "DATABASE_URL with a derived smarthris_test database."
        )

    parsed = urlparse(database_url)
    path = (parsed.path or "").lstrip("/")
    database_name = path.split("?")[0] if path else ""

    if not database_name:
        pytest.fail(f"Could not parse database name from URL: {database_url!r}")

    if database_name not in APPROVED_INTEGRATION_DATABASE_NAMES:
        pytest.fail(
            f"Refusing integration test setup on database {database_name!r}. "
            f"Approved names: {sorted(APPROVED_INTEGRATION_DATABASE_NAMES)}."
        )

    return database_url


def test_guard_rejects_production_database_name() -> None:
    with pytest.raises(Failed, match="Refusing integration test setup"):
        assert_safe_integration_database_url("mysql+pymysql://u:p@127.0.0.1:3306/smarthris")


def test_guard_accepts_approved_test_database() -> None:
    url = assert_safe_integration_database_url(
        "mysql+pymysql://u:p@127.0.0.1:3306/smarthris_test"
    )
    assert url.endswith("smarthris_test")

