"""Tests for environment-based configuration."""

import pytest

from smarthris.config import Settings, get_settings


def test_settings_load_from_explicit_values() -> None:
    settings = Settings(
        _env_file=None,
        app_name="My HRIS",
        database_url="mysql+pymysql://u:p@localhost:3306/hris",
    )
    assert settings.app_name == "My HRIS"
    assert settings.sqlalchemy_database_url == "mysql+pymysql://u:p@localhost:3306/hris"


def test_sqlalchemy_url_built_from_mysql_parts() -> None:
    settings = Settings(
        _env_file=None,
        database_url=None,
        mysql_host="db.internal",
        mysql_port=3307,
        mysql_user="app",
        mysql_password="secret",
        mysql_database="smarthris",
    )
    assert (
        settings.sqlalchemy_database_url
        == "mysql+pymysql://app:secret@db.internal:3307/smarthris"
    )


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Cached Name")
    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()
    assert first is second
    assert first.app_name == "Cached Name"
    get_settings.cache_clear()
