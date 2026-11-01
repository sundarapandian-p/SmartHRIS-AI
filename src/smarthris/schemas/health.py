"""Response models for operational endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


class DatabaseHealth(BaseModel):
    """Database connectivity as verified by a live query."""

    status: Literal["ok", "error"] = Field(
        description="'ok' only when SELECT 1 succeeds against MySQL.",
    )


class HealthResponse(BaseModel):
    """Application and dependency health."""

    status: Literal["ok"] = Field(description="API process is running.")
    database: DatabaseHealth
