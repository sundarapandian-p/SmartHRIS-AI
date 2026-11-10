"""Unit tests for the Streamlit API client (mocked HTTP only; no live API/DB)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import (
    DEFAULT_API_BASE_URL,
    NAV_BULK_IMPORT,
    NAV_DASHBOARD,
    NAV_EMPLOYEES,
    NAV_ITEMS,
    NAV_LMS_ANALYTICS,
)

_NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _employee_item(**overrides: object) -> dict:
    base = {
        "id": 1,
        "employee_number": "E001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada@example.com",
        "department_id": 1,
        "job_role": None,
        "job_title": None,
        "manager_employee_id": None,
        "hire_date": None,
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


def _import_job(**overrides: object) -> dict:
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "pending",
        "filename": "employees.csv",
        "file_type": "csv",
        "dry_run": False,
        "total_rows": 0,
        "processed_rows": 0,
        "success_count": 0,
        "failed_count": 0,
        "error_message": None,
        "parent_job_id": None,
        "created_at": _NOW,
        "started_at": None,
        "finished_at": None,
    }
    base.update(overrides)
    return base


def _client(handler) -> SmartHrisApiClient:
    return SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
    )


def test_nav_items_match_sidebar_labels() -> None:
    assert NAV_ITEMS == (NAV_DASHBOARD, NAV_EMPLOYEES, NAV_BULK_IMPORT, NAV_LMS_ANALYTICS)
    assert NAV_DASHBOARD == "Dashboard"
    assert NAV_EMPLOYEES == "Employees"
    assert NAV_BULK_IMPORT == "Bulk Import"
    assert NAV_LMS_ANALYTICS == "LMS Analytics"


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

    assert _client(handler).get_health() == payload


def test_get_health_http_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "unavailable"})

    with pytest.raises(ApiError) as exc_info:
        _client(handler).get_health()
    assert exc_info.value.status_code == 503
    assert "unavailable" in str(exc_info.value)


def test_get_health_connection_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiError, match="Cannot reach the API"):
        _client(handler).get_health()


def test_get_health_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ApiError, match="timed out"):
        _client(handler).get_health()


def test_get_health_rejects_non_object_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=json.dumps(["not", "an", "object"]),
            headers={"content-type": "application/json"},
        )

    with pytest.raises(ApiError, match="unexpected JSON payload"):
        _client(handler).get_health()


def test_get_health_rejects_invalid_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with pytest.raises(ApiError, match="unexpected format"):
        _client(handler).get_health()


def test_list_employees_success_and_query_params() -> None:
    captured: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/employees"
        captured.update(parse_qs(request.url.query.decode()))
        return httpx.Response(
            200,
            json={
                "items": [_employee_item()],
                "total": 1,
                "page": 2,
                "page_size": 10,
                "pages": 1,
            },
        )

    result = _client(handler).list_employees(
        page=2,
        page_size=10,
        employee_number="E0",
        name="Ada",
        first_name="Ada",
        last_name="Lovelace",
        work_email="ada@",
        department_id=3,
        status="active",
        sort_by="last_name",
        sort_order="desc",
    )
    assert result.total == 1
    assert result.items[0].employee_number == "E001"
    assert captured["page"] == ["2"]
    assert captured["page_size"] == ["10"]
    assert captured["employee_number"] == ["E0"]
    assert captured["name"] == ["Ada"]
    assert captured["first_name"] == ["Ada"]
    assert captured["last_name"] == ["Lovelace"]
    assert captured["work_email"] == ["ada@"]
    assert captured["department_id"] == ["3"]
    assert captured["status"] == ["active"]
    assert captured["sort_by"] == ["last_name"]
    assert captured["sort_order"] == ["desc"]


def test_list_employees_omits_none_filters() -> None:
    captured: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(parse_qs(request.url.query.decode()))
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0},
        )

    _client(handler).list_employees(page=1, page_size=20)
    assert "employee_number" not in captured
    assert "name" not in captured
    assert "department_id" not in captured
    assert "status" not in captured
    assert captured["page"] == ["1"]
    assert captured["page_size"] == ["20"]


def test_upload_import_csv_multipart() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/imports"
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data")
        body = request.content
        assert b'name="file"' in body
        assert b"employees.csv" in body
        assert b"Employee Number" in body
        assert b'name="dry_run"' in body
        assert b"false" in body
        return httpx.Response(201, json=_import_job())

    job = _client(handler).upload_import(
        filename="employees.csv",
        content=b"Employee Number,First Name\nE1,Ada\n",
        dry_run=False,
    )
    assert job.id == "11111111-1111-1111-1111-111111111111"
    assert job.filename == "employees.csv"
    assert job.dry_run is False


def test_upload_import_dry_run_true() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content
        assert b'name="dry_run"' in body
        assert b"true" in body
        return httpx.Response(201, json=_import_job(dry_run=True))

    job = _client(handler).upload_import(
        filename="batch.csv",
        content=b"a,b\n1,2\n",
        dry_run=True,
    )
    assert job.dry_run is True


def test_upload_import_requires_filename() -> None:
    with pytest.raises(ApiError, match="filename"):
        SmartHrisApiClient(base_url="http://test.local").upload_import(
            filename="  ",
            content=b"x",
        )


def test_get_import_job_status() -> None:
    job_id = "22222222-2222-2222-2222-222222222222"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == f"/imports/{job_id}"
        return httpx.Response(
            200,
            json=_import_job(
                id=job_id,
                status="completed",
                total_rows=10,
                processed_rows=10,
                success_count=9,
                failed_count=1,
            ),
        )

    job = _client(handler).get_import_job(job_id)
    assert job.status.value == "completed"
    assert job.success_count == 9
    assert job.failed_count == 1


def test_list_import_errors_pagination() -> None:
    job_id = "33333333-3333-3333-3333-333333333333"
    captured: dict[str, list[str]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/imports/{job_id}/errors"
        captured.update(parse_qs(request.url.query.decode()))
        return httpx.Response(
            200,
            json={
                "import_job_id": job_id,
                "items": [
                    {
                        "id": 1,
                        "row_number": 2,
                        "employee_number": "E2",
                        "field": "work_email",
                        "code": "invalid_email",
                        "message": "Invalid email",
                    }
                ],
                "total": 5,
                "page": 2,
                "page_size": 1,
                "pages": 5,
            },
        )

    result = _client(handler).list_import_errors(job_id, page=2, page_size=1)
    assert result.import_job_id == job_id
    assert result.total == 5
    assert result.items[0].code == "invalid_email"
    assert captured["page"] == ["2"]
    assert captured["page_size"] == ["1"]


def test_retry_import() -> None:
    parent_id = "44444444-4444-4444-4444-444444444444"
    child_id = "55555555-5555-5555-5555-555555555555"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/imports/{parent_id}/retry"
        return httpx.Response(
            201,
            json=_import_job(id=child_id, parent_job_id=parent_id, status="pending"),
        )

    child = _client(handler).retry_import(parent_id)
    assert child.id == child_id
    assert child.parent_job_id == parent_id


@pytest.mark.parametrize(
    ("status_code", "body", "expected_fragment", "expected_code"),
    [
        (400, {"detail": "Only CSV uploads are supported", "code": "validation_error"}, "CSV", "validation_error"),
        (404, {"detail": "Import job not found", "code": "not_found"}, "not found", "not_found"),
        (409, {"detail": "Retry is only allowed for completed or failed jobs", "code": "conflict"}, "Retry", "conflict"),
        (413, {"detail": "Uploaded file exceeds the maximum allowed size", "code": "payload_too_large"}, "maximum", "payload_too_large"),
        (
            422,
            {
                "detail": [
                    {
                        "type": "int_parsing",
                        "loc": ["query", "page"],
                        "msg": "Input should be a valid integer",
                    }
                ]
            },
            "page",
            "validation_error",
        ),
        (500, {"detail": "Failed to store uploaded file", "code": "storage_error"}, "store", "storage_error"),
    ],
)
def test_http_error_mapping(
    status_code: int,
    body: dict,
    expected_fragment: str,
    expected_code: str | None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    with pytest.raises(ApiError) as exc_info:
        _client(handler).get_import_job("missing")
    assert exc_info.value.status_code == status_code
    assert expected_fragment.lower() in str(exc_info.value).lower()
    if expected_code is not None:
        assert exc_info.value.code == expected_code


def test_http_error_fallback_without_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(413, content=b"too large", headers={"content-type": "text/plain"})

    with pytest.raises(ApiError) as exc_info:
        _client(handler).upload_import(filename="x.csv", content=b"a")
    assert exc_info.value.status_code == 413
    assert "maximum allowed size" in str(exc_info.value).lower()


def test_list_employees_connection_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ApiError, match="Cannot reach the API"):
        _client(handler).list_employees()


def test_upload_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.WriteTimeout("upload stalled", request=request)

    with pytest.raises(ApiError, match="timed out"):
        _client(handler).upload_import(filename="x.csv", content=b"a,b\n")


def test_unexpected_success_status_for_upload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_import_job())

    with pytest.raises(ApiError, match="Unexpected HTTP status 200"):
        _client(handler).upload_import(filename="x.csv", content=b"a,b\n")


def test_non_json_success_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    with pytest.raises(ApiError, match="non-JSON"):
        _client(handler).get_health()
