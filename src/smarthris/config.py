"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration; secrets live in .env (see .env.example)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="SmartHRIS-AI", validation_alias="APP_NAME")
    app_env: Literal["development", "staging", "production"] = Field(
        default="development",
        validation_alias="APP_ENV",
    )
    debug: bool = Field(default=False, validation_alias="DEBUG")

    api_host: str = Field(default="127.0.0.1", validation_alias="API_HOST")
    api_port: int = Field(default=8000, validation_alias="API_PORT")

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    mysql_host: str = Field(default="127.0.0.1", validation_alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, validation_alias="MYSQL_PORT")
    mysql_user: str = Field(default="smarthris", validation_alias="MYSQL_USER")
    mysql_password: str = Field(default="", validation_alias="MYSQL_PASSWORD")
    mysql_database: str = Field(default="smarthris", validation_alias="MYSQL_DATABASE")

    test_database_url: str | None = Field(default=None, validation_alias="TEST_DATABASE_URL")

    import_batch_size: int = Field(default=500, validation_alias="IMPORT_BATCH_SIZE")
    max_upload_bytes: int = Field(
        default=52_428_800,
        validation_alias="MAX_UPLOAD_BYTES",
        description="Maximum upload size in bytes (default 50 MiB).",
    )
    import_temp_dir: str = Field(
        default="data/imports",
        validation_alias="IMPORT_TEMP_DIR",
        description="Private directory for temporary upload files (not web-served).",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_url(self) -> str:
        """SQLAlchemy URL for MySQL via PyMySQL."""
        if self.database_url:
            return self.database_url
        password = self.mysql_password
        return (
            f"mysql+pymysql://{self.mysql_user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_test_database_url(self) -> str:
        """Database URL for pytest integration tests (separate from production data)."""
        if self.test_database_url:
            return self.test_database_url
        base_url, _ = self.sqlalchemy_database_url.rsplit("/", 1)
        return f"{base_url}/smarthris_test"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance for dependency injection and Alembic."""
    return Settings()
