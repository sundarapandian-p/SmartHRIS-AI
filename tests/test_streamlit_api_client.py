"""Unit tests for the Streamlit API client foundation (no live network)."""

from __future__ import annotations

import json

import httpx
import pytest

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import (
    DEFAULT_API_BASE_URL,
    NAV_BULK_IMPORT,
    NAV_DASHBOARD,
    NAV_EMPLOYEES,
    NAV_ITEMS,
)


def test_nav_items_match_sidebar_labels() -> None:
    assert NAV_ITEMS == (NAV_DASHBOARD, NAV_EMPLOYEES, NAV_BULK_IMPORT)
    assert NAV_DASHBOARD == "Dashboard"
    assert NAV_EMPLOYEES == "Employees"
    assert NAV_BULK_IMPORT == "Bulk Import"


def test_default_base_url_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_BASE_URL", raising=False)
    client = SmartHrisApiClient(base_url=None)
    assert client.base_url == DEFAULT_API_BASE_URL


def test_base_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "http://127.0.0.1:9000")
    client = SmartHrisApiClient(base_url=None)
    assert client.base_url == "http://127.0.0.1:9000"


def test_base_url_strips_trailing_slash() -> None:
    client = SmartHrisApiClient(base_url="http://127.0.0.1:8000/")
    assert client.base_url == "http://127.0.0.1:8000"


def test_get_health_success() -> None:
    payload = {"status": "ok", "database": {"status": "ok"}}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=transport,
    )
    assert client.get_health() == payload


def test_get_health_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiError) as exc_info:
        client.get_health()
    assert exc_info.value.status_code == 503


def test_get_health_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiError, match="Cannot reach API"):
        client.get_health()


def test_get_health_rejects_non_object_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(["not", "an", "object"]),
            headers={"content-type": "application/json"},
        )

    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiError, match="non-object"):
        client.get_health()
