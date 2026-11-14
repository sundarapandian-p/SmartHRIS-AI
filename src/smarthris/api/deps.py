"""Shared FastAPI dependencies."""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from smarthris.config import Settings, get_settings
from smarthris.core.exceptions import ForbiddenError, UnauthenticatedError, ValidationError
from smarthris.models.user import User, UserRole
from smarthris.repositories.user_repository import UserRepository

__all__ = [
    "Settings",
    "get_settings",
    "get_db",
    "get_current_user",
    "get_current_active_user",
    "require_roles",
    "RequireHrOrAdmin",
]

_FORBIDDEN_MESSAGE = "You do not have permission to perform this action"

_http_bearer = HTTPBearer(auto_error=False)


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


def _settings_from_request(request: Request) -> Settings:
    return request.app.state.settings


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_http_bearer)],
    db: Session = Depends(get_db),
) -> User:
    """Resolve the authenticated user from a Bearer JWT (role loaded from DB)."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthenticatedError("Not authenticated")

    token = credentials.credentials.strip()
    if not token:
        raise UnauthenticatedError("Not authenticated")

    settings = _settings_from_request(request)
    from smarthris.core.security import decode_access_token

    try:
        user_id = decode_access_token(token, settings=settings)
    except ValidationError:
        raise UnauthenticatedError("Could not validate credentials") from None

    user = UserRepository(db).get_by_id(user_id)
    if user is None:
        raise UnauthenticatedError("Could not validate credentials")

    return user


def get_current_active_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not user.is_active:
        raise ForbiddenError("Inactive user account")
    return user


def require_roles(*roles: UserRole):
    """Dependency factory: active user must have one of ``roles`` (from database)."""
    allowed = frozenset(roles)

    def _dependency(
        user: Annotated[User, Depends(get_current_active_user)],
    ) -> User:
        if user.role not in allowed:
            raise ForbiddenError(_FORBIDDEN_MESSAGE)
        return user

    return _dependency


RequireHrOrAdmin = require_roles(UserRole.ADMIN, UserRole.HR)