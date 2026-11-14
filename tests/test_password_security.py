"""Unit tests for password hashing and JWT helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("bcrypt")
pytest.importorskip("jwt")

from smarthris.config import Settings
from smarthris.core.exceptions import ServiceUnavailableError, ValidationError
from smarthris.core.email import normalize_email
from smarthris.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

_TEST_SECRET = "pytest-auth-secret-key-at-least-32-chars"


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "jwt_secret_key": _TEST_SECRET,
        "jwt_algorithm": "HS256",
        "jwt_access_token_expire_minutes": 30,
    }
    base.update(overrides)
    return Settings(**base)


def test_normalize_email() -> None:
    assert normalize_email("  Admin@Example.COM ") == "admin@example.com"


def test_password_hash_and_verify() -> None:
    digest = hash_password("correct-horse-battery")
    assert digest.startswith("$2")
    assert verify_password("correct-horse-battery", digest)
    assert not verify_password("wrong-password", digest)


def test_empty_password_rejected() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        hash_password("")


def test_password_over_72_ascii_bytes_rejected() -> None:
    with pytest.raises(ValidationError, match="72 bytes"):
        hash_password("a" * 73)


def test_password_over_72_utf8_bytes_rejected() -> None:
    long_unicode = "é" * 37
    with pytest.raises(ValidationError, match="72 bytes"):
        hash_password(long_unicode)


def test_verify_password_rejects_over_72_bytes() -> None:
    digest = hash_password("short-password")
    assert verify_password("a" * 73, digest) is False
    assert verify_password("é" * 37, digest) is False


def test_jwt_create_and_decode() -> None:
    settings = _settings()
    token, expires_in = create_access_token(user_id=42, settings=settings)
    assert expires_in == 30 * 60
    assert decode_access_token(token, settings=settings) == 42


def test_jwt_expired_rejected() -> None:
    import jwt

    settings = _settings()
    past = datetime.now(tz=UTC) - timedelta(minutes=5)
    payload = {
        "sub": "7",
        "iat": int(past.timestamp()) - 120,
        "exp": int(past.timestamp()),
    }
    token = jwt.encode(payload, _TEST_SECRET, algorithm="HS256")
    with pytest.raises(ValidationError, match="expired"):
        decode_access_token(token, settings=settings)


def test_jwt_invalid_signature_rejected() -> None:
    settings = _settings()
    token, _ = create_access_token(user_id=1, settings=settings)
    other_settings = _settings(jwt_secret_key="different-secret-key-32-characters!!")
    with pytest.raises(ValidationError, match="Invalid access token"):
        decode_access_token(token, settings=other_settings)


def test_jwt_requires_configured_secret() -> None:
    settings = _settings(jwt_secret_key="short")
    with pytest.raises(ServiceUnavailableError, match="temporarily unavailable"):
        create_access_token(user_id=1, settings=settings)


@pytest.mark.parametrize(
    ("sub",),
    [
        ("0",),
        ("-1",),
        ("",),
        ("not-a-number",),
    ],
)
def test_jwt_rejects_invalid_sub_claim(sub: str) -> None:
    import jwt

    settings = _settings()
    now = datetime.now(tz=UTC)
    token = jwt.encode(
        {
            "sub": sub,
            "iat": int(now.timestamp()),
            "exp": int(now.timestamp()) + 3600,
        },
        _TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(ValidationError, match="Invalid access token"):
        decode_access_token(token, settings=settings)