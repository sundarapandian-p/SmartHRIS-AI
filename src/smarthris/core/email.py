"""Email normalization and validation for API/import contracts."""

from email_validator import EmailNotValidError, validate_email


def normalize_email(email: str) -> str:
    """Lowercase and trim for consistent lookup and storage."""
    return email.strip().lower()


def allows_reserved_email_domains() -> bool:
    """Allow RFC special-use domains (e.g. .test) outside production."""
    from smarthris.config import get_settings

    return get_settings().app_env != "production"


def validate_app_email(value: object) -> str:
    """Validate and normalize an email for Pydantic schemas."""
    if not isinstance(value, str):
        msg = "value is not a valid email address"
        raise TypeError(msg)
    cleaned = normalize_email(value)
    if not cleaned:
        raise ValueError("value is not a valid email address")
    try:
        result = validate_email(
            cleaned,
            check_deliverability=False,
            test_environment=allows_reserved_email_domains(),
        )
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return result.normalized