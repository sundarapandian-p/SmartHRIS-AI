"""Tests for the /health endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient


def test_health_reports_database_ok_when_connection_verified(client: TestClient) -> None:
    with patch("smarthris.api.routers.health.verify_database_connection", return_value=True):
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "database": {"status": "ok"}}


def test_health_reports_database_error_when_connection_fails(client: TestClient) -> None:
    with patch("smarthris.api.routers.health.verify_database_connection", return_value=False):
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "error"


def test_verify_database_connection_returns_false_on_failure() -> None:
    from sqlalchemy import create_engine

    from smarthris.db.session import verify_database_connection

    engine = create_engine("mysql+pymysql://invalid:invalid@127.0.0.1:59999/none")
    assert verify_database_connection(engine) is False
