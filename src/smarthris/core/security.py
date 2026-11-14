"""Password hashing and JWT access tokens (no logging of secrets)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from smarthris.config import JWT_ALGORITHM, Settings
from smarthris.core.exceptions import ServiceUnavailableError, ValidationError

_MIN_JWT_SECRET_LENGTH = 32
_MAX_BCRYPT_PASSWORD_BYTES = 72


def ensure_jwt_configured(settings: Settings) -> None:
    """Raise ``ServiceUnavailableError`` when JWT signing is not configured."""
    _require_jwt_secret(settings)


def _require_jwt_secret(settings: Settings) -> str:
    secret = settings.jwt_secret_key.strip()
    if len(secret) < _MIN_JWT_SECRET_LENGTH:
        raise ServiceUnavailableError(
            "Authentication is temporarily unavailable",
            code="jwt_misconfigured",
        )
    return secret


def _password_bytes(plain_password: str) -> bytes:
    if not plain_password:
        raise ValidationError("Password must not be empty")
    password_bytes = plain_password.encode("utf-8")
    if len(password_bytes) > _MAX_BCRYPT_PASSWORD_BYTES:
        raise ValidationError("Password must be at most 72 bytes")
    return password_bytes


def hash_password(plain_password: str) -> str:
    """Hash a password with bcrypt (never store or log plaintext)."""
    import bcrypt

    password_bytes = _password_bytes(plain_password)
    salt = bcrypt.gensalt()
    digest = bcrypt.hashpw(password_bytes, salt)
    return digest.decode("ascii")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Return True when ``plain_password`` matches ``password_hash``."""
    import bcrypt

    if not plain_password or not password_hash:
        return False
    try:
        password_bytes = _password_bytes(plain_password)
    except ValidationError:
        return False
    try:
        return bcrypt.checkpw(
            password_bytes,
            password_hash.encode("ascii"),
        )
    except (ValueError, TypeError):
        return False


def create_access_token(
    *,
    user_id: int,
    settings: Settings,
) -> tuple[str, int]:
    """
    Build a signed JWT access token.

    Returns ``(token, expires_in_seconds)``. Only ``sub`` (user id) is embedded;
    authorization must load role from the database.
    """
    import jwt

    secret = _require_jwt_secret(settings)
    expires_delta = timedelta(minutes=settings.jwt_access_token_expire_minutes)
    now = datetime.now(tz=UTC)
    expire = now + expires_delta
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(
        payload,
        secret,
        algorithm=JWT_ALGORITHM,
    )
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str, *, settings: Settings) -> int:
    """
    Validate signature and expiration; return the user id from ``sub``.

    Raises ``ValidationError`` for malformed, expired, or invalid tokens.
    """
    import jwt

    secret = _require_jwt_secret(settings)
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ValidationError("Access token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValidationError("Invalid access token") from exc

    sub = payload.get("sub")
    if sub is None or (isinstance(sub, str) and not sub.strip()):
        raise ValidationError("Invalid access token")
    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid access token") from exc
    if user_id <= 0:
        raise ValidationError("Invalid access token")
    return user_id