"""Thin httpx client for the SmartHRIS FastAPI backend.

Business rules stay on the API. This module only performs HTTP calls and
maps responses to the existing Pydantic API contracts.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any, BinaryIO

import httpx
from pydantic import BaseModel, ValidationError

from smarthris.schemas.employee import EmployeeListResponse, EmployeeSortField, SortOrder
from smarthris.schemas.health import HealthResponse
from smarthris.schemas.import_job import ImportErrorListResponse, ImportJobRead
from smarthris.schemas.lms_analytics import (
    CourseAnalyticsListResponse,
    CourseAnalyticsSortField,
    EmployeeAnalyticsListResponse,
    EmployeeAnalyticsSortField,
    EnrolmentTrendsResponse,
    LmsSummaryResponse,
    TopCoursesResponse,
    TrendGranularity,
)
from streamlit_app.constants import DEFAULT_API_BASE_URL

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

_STATUS_FALLBACKS: Mapping[int, str] = {
    400: "The request was invalid.",
    404: "The requested resource was not found.",
    409: "The request conflicts with the current state of the resource.",
    413: "The uploaded file exceeds the maximum allowed size.",
    422: "The request failed validation.",
    500: "The API encountered an unexpected server error.",
    502: "The API gateway returned a bad gateway error.",
    503: "The API is temporarily unavailable.",
}


class ApiError(Exception):
    """Raised when the API returns an error or is unreachable."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SmartHrisApiClient:
    """Reusable FastAPI client for the Streamlit frontend."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float | httpx.Timeout = DEFAULT_TIMEOUT,
        upload_timeout: float | httpx.Timeout = UPLOAD_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved = (base_url or os.environ.get("API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip(
            "/"
        )
        self.base_url = resolved
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self._transport = transport

    def _client(self, *, timeout: float | httpx.Timeout | None = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout if timeout is None else timeout,
            transport=self._transport,
        )

    @staticmethod
    def _fallback_message(status_code: int) -> str:
        return _STATUS_FALLBACKS.get(
            status_code,
            f"Request failed with HTTP {status_code}.",
        )

    @classmethod
    def _parse_error_payload(cls, response: httpx.Response) -> tuple[str, str | None]:
        """Extract a safe user-facing message and optional machine code."""
        code: str | None = None
        try:
            body = response.json()
        except ValueError:
            return cls._fallback_message(response.status_code), None

        if not isinstance(body, dict):
            return cls._fallback_message(response.status_code), None

        raw_code = body.get("code")
        if isinstance(raw_code, str) and raw_code.strip():
            code = raw_code.strip()

        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip(), code

        if isinstance(detail, list):
            parts: list[str] = []
            for item in detail[:8]:
                if not isinstance(item, dict):
                    continue
                msg = item.get("msg") or item.get("message")
                if not isinstance(msg, str) or not msg.strip():
                    continue
                loc = item.get("loc")
                if isinstance(loc, list) and loc:
                    # Skip leading "body" / "query" / "path" noise when present.
                    loc_parts = [str(part) for part in loc if part not in {"body", "query", "path"}]
                    prefix = ".".join(loc_parts) if loc_parts else ""
                    parts.append(f"{prefix}: {msg}" if prefix else msg)
                else:
                    parts.append(msg)
            if parts:
                return "; ".join(parts), code or "validation_error"

        if isinstance(detail, dict):
            nested = detail.get("message") or detail.get("msg")
            if isinstance(nested, str) and nested.strip():
                return nested.strip(), code

        return cls._fallback_message(response.status_code), code

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        message, code = self._parse_error_payload(response)
        raise ApiError(message, status_code=response.status_code, code=code)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
        timeout: float | httpx.Timeout | None = None,
        expected_status: int | set[int] | None = None,
    ) -> dict[str, Any]:
        try:
            with self._client(timeout=timeout) as client:
                response = client.request(
                    method,
                    path,
                    params=params,
                    data=data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise ApiError(
                "The API request timed out. Confirm the backend is running and try again.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(
                f"Cannot reach the API at {self.base_url}. "
                "Confirm the backend is running and API_BASE_URL is correct.",
            ) from exc

        self._raise_for_status(response)

        if expected_status is not None:
            allowed = (
                {expected_status} if isinstance(expected_status, int) else set(expected_status)
            )
            if response.status_code not in allowed:
                raise ApiError(
                    f"Unexpected HTTP status {response.status_code} from {path}.",
                    status_code=response.status_code,
                )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiError(
                "The API returned a non-JSON response.",
                status_code=response.status_code,
            ) from exc

        if not isinstance(payload, dict):
            raise ApiError(
                "The API returned an unexpected JSON payload (expected an object).",
                status_code=response.status_code,
            )
        return payload

    @staticmethod
    def _omit_none(params: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if value is not None}

    @staticmethod
    def _validate_model[T: BaseModel](
        model_type: type[T],
        payload: dict[str, Any],
        *,
        status_code: int | None = None,
    ) -> T:
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise ApiError(
                "The API returned data in an unexpected format.",
                status_code=status_code,
            ) from exc

    def get_health(self) -> dict[str, Any]:
        """GET /health — used by the Dashboard connectivity check.

        Returns a plain dict so existing Streamlit dashboard code can call
        ``st.json(health)`` without changes.
        """
        payload = self._request_json("GET", "/health", expected_status=200)
        return self._validate_model(HealthResponse, payload, status_code=200).model_dump(
            mode="json"
        )

    def list_employees(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        employee_number: str | None = None,
        name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        work_email: str | None = None,
        department_id: int | None = None,
        status: str | None = None,
        sort_by: str | EmployeeSortField = EmployeeSortField.ID,
        sort_order: str | SortOrder = SortOrder.ASC,
    ) -> EmployeeListResponse:
        """GET /employees with pagination and allowlisted filters/sort."""
        sort_by_value = sort_by.value if isinstance(sort_by, EmployeeSortField) else sort_by
        sort_order_value = sort_order.value if isinstance(sort_order, SortOrder) else sort_order
        params = self._omit_none(
            {
                "page": page,
                "page_size": page_size,
                "employee_number": employee_number,
                "name": name,
                "first_name": first_name,
                "last_name": last_name,
                "work_email": work_email,
                "department_id": department_id,
                "status": status,
                "sort_by": sort_by_value,
                "sort_order": sort_order_value,
            }
        )
        payload = self._request_json("GET", "/employees", params=params, expected_status=200)
        return self._validate_model(EmployeeListResponse, payload, status_code=200)

    def upload_import(
        self,
        *,
        filename: str,
        content: bytes | BinaryIO,
        dry_run: bool = False,
    ) -> ImportJobRead:
        """POST /imports — multipart CSV upload with optional dry_run form field."""
        return self._upload_import_csv(
            path="/imports",
            filename=filename,
            content=content,
            dry_run=dry_run,
        )

    def upload_course_import(
        self,
        *,
        filename: str,
        content: bytes | BinaryIO,
        dry_run: bool = False,
    ) -> ImportJobRead:
        """POST /imports/courses — course CSV multipart upload."""
        return self._upload_import_csv(
            path="/imports/courses",
            filename=filename,
            content=content,
            dry_run=dry_run,
        )

    def upload_enrolment_import(
        self,
        *,
        filename: str,
        content: bytes | BinaryIO,
        dry_run: bool = False,
    ) -> ImportJobRead:
        """POST /imports/enrolments — enrolment CSV multipart upload."""
        return self._upload_import_csv(
            path="/imports/enrolments",
            filename=filename,
            content=content,
            dry_run=dry_run,
        )

    def _upload_import_csv(
        self,
        *,
        path: str,
        filename: str,
        content: bytes | BinaryIO,
        dry_run: bool,
    ) -> ImportJobRead:
        if not filename or not str(filename).strip():
            raise ApiError("A CSV filename is required for upload.")
        files = {
            "file": (filename, content, "text/csv"),
        }
        data = {"dry_run": "true" if dry_run else "false"}
        payload = self._request_json(
            "POST",
            path,
            data=data,
            files=files,
            timeout=self.upload_timeout,
            expected_status=201,
        )
        return self._validate_model(ImportJobRead, payload, status_code=201)

    def get_import_job(self, job_id: str) -> ImportJobRead:
        """GET /imports/{job_id}."""
        payload = self._request_json(
            "GET",
            f"/imports/{job_id}",
            expected_status=200,
        )
        return self._validate_model(ImportJobRead, payload, status_code=200)

    def list_import_errors(
        self,
        job_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> ImportErrorListResponse:
        """GET /imports/{job_id}/errors with pagination."""
        params = {"page": page, "page_size": page_size}
        payload = self._request_json(
            "GET",
            f"/imports/{job_id}/errors",
            params=params,
            expected_status=200,
        )
        return self._validate_model(ImportErrorListResponse, payload, status_code=200)

    def retry_import(self, job_id: str) -> ImportJobRead:
        """POST /imports/{job_id}/retry."""
        payload = self._request_json(
            "POST",
            f"/imports/{job_id}/retry",
            expected_status=201,
        )
        return self._validate_model(ImportJobRead, payload, status_code=201)

    def get_lms_summary(self) -> LmsSummaryResponse:
        """GET /analytics/lms/summary."""
        payload = self._request_json(
            "GET",
            "/analytics/lms/summary",
            expected_status=200,
        )
        return self._validate_model(LmsSummaryResponse, payload, status_code=200)

    def list_lms_courses(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        category: str | None = None,
        course_status: str | None = None,
        sort_by: str | CourseAnalyticsSortField = CourseAnalyticsSortField.ENROLMENTS,
        sort_order: str | SortOrder = SortOrder.DESC,
    ) -> CourseAnalyticsListResponse:
        """GET /analytics/lms/courses."""
        sort_by_value = (
            sort_by.value if isinstance(sort_by, CourseAnalyticsSortField) else sort_by
        )
        sort_order_value = sort_order.value if isinstance(sort_order, SortOrder) else sort_order
        params = self._omit_none(
            {
                "page": page,
                "page_size": page_size,
                "category": category,
                "course_status": course_status,
                "sort_by": sort_by_value,
                "sort_order": sort_order_value,
            }
        )
        payload = self._request_json(
            "GET",
            "/analytics/lms/courses",
            params=params,
            expected_status=200,
        )
        return self._validate_model(CourseAnalyticsListResponse, payload, status_code=200)

    def list_lms_top_courses(
        self,
        *,
        limit: int = 10,
        status_filter: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> TopCoursesResponse:
        """GET /analytics/lms/courses/top."""
        params = self._omit_none(
            {
                "limit": limit,
                "status_filter": status_filter,
                "from_date": from_date,
                "to_date": to_date,
            }
        )
        payload = self._request_json(
            "GET",
            "/analytics/lms/courses/top",
            params=params,
            expected_status=200,
        )
        return self._validate_model(TopCoursesResponse, payload, status_code=200)

    def list_lms_employees(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        department_id: int | None = None,
        employee_status: str | None = None,
        sort_by: str | EmployeeAnalyticsSortField = EmployeeAnalyticsSortField.ENROLMENTS,
        sort_order: str | SortOrder = SortOrder.DESC,
    ) -> EmployeeAnalyticsListResponse:
        """GET /analytics/lms/employees."""
        sort_by_value = (
            sort_by.value
            if isinstance(sort_by, EmployeeAnalyticsSortField)
            else sort_by
        )
        sort_order_value = sort_order.value if isinstance(sort_order, SortOrder) else sort_order
        params = self._omit_none(
            {
                "page": page,
                "page_size": page_size,
                "department_id": department_id,
                "employee_status": employee_status,
                "sort_by": sort_by_value,
                "sort_order": sort_order_value,
            }
        )
        payload = self._request_json(
            "GET",
            "/analytics/lms/employees",
            params=params,
            expected_status=200,
        )
        return self._validate_model(EmployeeAnalyticsListResponse, payload, status_code=200)

    def list_lms_enrolment_trends(
        self,
        *,
        from_date: str,
        to_date: str,
        granularity: str | TrendGranularity = TrendGranularity.DAY,
        status: str | None = None,
        course_code: str | None = None,
    ) -> EnrolmentTrendsResponse:
        """GET /analytics/lms/trends/enrolments."""
        granularity_value = (
            granularity.value if isinstance(granularity, TrendGranularity) else granularity
        )
        params = self._omit_none(
            {
                "from_date": from_date,
                "to_date": to_date,
                "granularity": granularity_value,
                "status": status,
                "course_code": course_code,
            }
        )
        payload = self._request_json(
            "GET",
            "/analytics/lms/trends/enrolments",
            params=params,
            expected_status=200,
        )
        return self._validate_model(EnrolmentTrendsResponse, payload, status_code=200)
