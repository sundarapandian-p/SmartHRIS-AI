"""Thin httpx client for the SmartHRIS FastAPI backend.

Business rules stay on the API. This module only performs HTTP calls.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from streamlit_app.constants import DEFAULT_API_BASE_URL


class ApiError(Exception):
    """Raised when the API returns an error or is unreachable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SmartHrisApiClient:
    """Minimal FastAPI client used by the Streamlit shell."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved = (base_url or os.environ.get("API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip(
            "/"
        )
        self.base_url = resolved
        self.timeout = timeout
        self._transport = transport

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self._transport,
        )

    def get_health(self) -> dict[str, Any]:
        """GET /health — used by the Dashboard connectivity check."""
        try:
            with self._client() as client:
                response = client.get("/health")
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach API at {self.base_url}: {exc}") from exc

        if response.status_code >= 400:
            raise ApiError(
                f"Health check failed with HTTP {response.status_code}",
                status_code=response.status_code,
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise ApiError("Health check returned a non-object JSON payload")
        return payload
