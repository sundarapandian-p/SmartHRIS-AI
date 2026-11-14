"""Reusable Pydantic email type with environment-aware reserved-domain rules."""

from typing import Annotated

from pydantic import BeforeValidator

from smarthris.core.email import validate_app_email


def _coerce_app_email(value: object) -> str:
    return validate_app_email(value)


AppEmailStr = Annotated[str, BeforeValidator(_coerce_app_email)]