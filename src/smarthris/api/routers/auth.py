"""Authentication routes (login and current user profile)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from smarthris.api.deps import get_current_active_user, get_db
from smarthris.config import Settings
from smarthris.models.user import User
from smarthris.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse
from smarthris.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def get_auth_service(request: Request, db: Session = Depends(get_db)) -> AuthService:
    return AuthService(db, _settings_from_app(request))


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Exchange email and password for a JWT access token."""
    return service.login(payload)


@router.get("/me", response_model=CurrentUserResponse)
def current_user(
    user: User = Depends(get_current_active_user),
) -> CurrentUserResponse:
    """Return the authenticated user's profile (role from database)."""
    return AuthService.to_current_user(user)