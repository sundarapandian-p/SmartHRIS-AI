"""Data access for application users."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from smarthris.core.email import normalize_email
from smarthris.core.user_validation import validate_user_employee_link
from smarthris.models.user import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def add(self, user: User) -> User:
        """Persist a new user after role/employee_id validation."""
        validate_user_employee_link(role=user.role, employee_id=user.employee_id)
        self._session.add(user)
        self._session.flush()
        return user

    def get_by_email(self, email: str) -> User | None:
        """Lookup by normalized email address."""
        needle = normalize_email(email)
        if not needle:
            return None
        stmt = select(User).where(User.email == needle)
        return self._session.scalars(stmt).one_or_none()

    def update_last_login_at(self, user: User) -> None:
        user.last_login_at = datetime.now(tz=UTC)
        self._session.flush()