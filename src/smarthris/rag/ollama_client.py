"""HTTP client for the local Ollama chat API."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from smarthris.config import Settings
from smarthris.core.exceptions import ServiceUnavailableError

_CHAT_PATH = "/api/chat"
_CONNECT_TIMEOUT_SECONDS = 5.0


class OllamaChatClient:
    """Sync Ollama chat client with injectable transport for tests."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        timeout = httpx.Timeout(
            settings.ollama_request_timeout_seconds,
            connect=_CONNECT_TIMEOUT_SECONDS,
        )
        if client is not None:
            self._client = client
        else:
            self._client = httpx.Client(
                base_url=settings.ollama_base_url.rstrip("/"),
                timeout=timeout,
                transport=transport,
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def chat(self, messages: Sequence[dict[str, str]]) -> str:
        """
        Send a non-streaming chat request and return assistant text.

        Does not log message bodies. Maps transport and HTTP failures to
        ``ServiceUnavailableError`` with stable client-facing codes.
        """
        payload = {
            "model": self._settings.ollama_model,
            "messages": list(messages),
            "stream": False,
        }
        try:
            response = self._client.post(_CHAT_PATH, json=payload)
        except httpx.TimeoutException as exc:
            raise ServiceUnavailableError(
                "The language model request timed out",
                code="ollama_timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(
                "The language model service is unavailable",
                code="ollama_unavailable",
            ) from exc

        if response.status_code == 404:
            raise ServiceUnavailableError(
                "The configured language model is not available",
                code="ollama_model_error",
            )

        if response.status_code >= 400:
            raise ServiceUnavailableError(
                "The language model service is unavailable",
                code="ollama_unavailable",
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ServiceUnavailableError(
                "The language model returned an invalid response",
                code="ollama_unavailable",
            ) from exc

        if not isinstance(body, dict):
            raise ServiceUnavailableError(
                "The language model returned an invalid response",
                code="ollama_unavailable",
            )

        content = _extract_assistant_content(body)
        if content is None:
            raise ServiceUnavailableError(
                "The language model returned an invalid response",
                code="ollama_unavailable",
            )
        return content


def _extract_assistant_content(body: dict[str, Any]) -> str | None:
    message = body.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    response_text = body.get("response")
    if isinstance(response_text, str) and response_text.strip():
        return response_text.strip()
    return None