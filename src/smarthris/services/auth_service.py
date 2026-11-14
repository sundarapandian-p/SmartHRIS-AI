"""Authentication: credential verification and access token issuance."""

from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import UnauthenticatedError
from smarthris.core.security import create_access_token, ensure_jwt_configured, verify_password
from smarthris.models.user import User
from smarthris.repositories.user_repository import UserRepository
from smarthris.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse

_INVALID_CREDENTIALS = "Invalid email or password"


class AuthService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._users = UserRepository(session)

    def login(self, payload: LoginRequest) -> TokenResponse:
        user = self._users.get_by_email(str(payload.email))
        if user is None or not verify_password(payload.password, user.password_hash):
            raise UnauthenticatedError(_INVALID_CREDENTIALS)

        if not user.is_active:
            raise UnauthenticatedError(_INVALID_CREDENTIALS)

        ensure_jwt_configured(self._settings)
        token, expires_in = create_access_token(user_id=user.id, settings=self._settings)
        self._users.update_last_login_at(user)
        self._session.commit()
        self._session.refresh(user)

        return TokenResponse(
            access_token=token,
            token_type="bearer",
            expires_in=expires_in,
        )

    @staticmethod
    def to_current_user(user: User) -> CurrentUserResponse:
        return CurrentUserResponse.model_validate(user)