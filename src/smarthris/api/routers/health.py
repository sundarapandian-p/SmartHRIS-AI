"""Health check routes."""

from fastapi import APIRouter, Request

from smarthris.db.session import verify_database_connection
from smarthris.schemas.health import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health_check(request: Request) -> HealthResponse:
    """Report API status and verified MySQL connectivity."""
    engine = request.app.state.db_engine
    db_ok = verify_database_connection(engine)
    return HealthResponse(
        status="ok",
        database=DatabaseHealth(status="ok" if db_ok else "error"),
    )
