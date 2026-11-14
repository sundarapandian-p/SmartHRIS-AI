"""Authentication API contracts (no password hashes or secrets)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from smarthris.models.user import UserRole
from smarthris.schemas.app_email import AppEmailStr


class LoginRequest(BaseModel):
    """Credentials for POST /auth/login."""

    email: AppEmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """OAuth2-style access token payload."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(ge=1, description="Token lifetime in seconds")


class CurrentUserResponse(BaseModel):
    """Authenticated user profile for GET /auth/me."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: AppEmailStr
    role: UserRole
    employee_id: int | None
    is_active: bool
    last_login_at: datetime | None