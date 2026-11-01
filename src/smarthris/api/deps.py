"""Shared FastAPI dependencies."""

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from smarthris.config import Settings, get_settings

__all__ = ["Settings", "get_settings", "get_db"]


def get_db(request: Request) -> Generator[Session, None, None]:
    """Yield a request-scoped SQLAlchemy session from the app lifespan factory."""
    session_factory = request.app.state.db_session_factory
    db = session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
