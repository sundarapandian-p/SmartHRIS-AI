"""Tests for environment-based configuration."""

import pytest
from pydantic import ValidationError

from smarthris.config import Settings, get_settings
from smarthris.rag import config as rag_defaults


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


def test_rag_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.ollama_base_url == rag_defaults.DEFAULT_OLLAMA_BASE_URL
    assert settings.ollama_model == rag_defaults.DEFAULT_OLLAMA_MODEL
    assert settings.ollama_request_timeout_seconds == rag_defaults.DEFAULT_OLLAMA_REQUEST_TIMEOUT_SECONDS
    assert settings.embedding_model_name == rag_defaults.DEFAULT_EMBEDDING_MODEL_NAME
    assert settings.rag_document_dir == rag_defaults.DEFAULT_RAG_DOCUMENT_DIR
    assert settings.rag_index_dir == rag_defaults.DEFAULT_RAG_INDEX_DIR
    assert settings.rag_chunk_size == rag_defaults.DEFAULT_RAG_CHUNK_SIZE
    assert settings.rag_chunk_overlap == rag_defaults.DEFAULT_RAG_CHUNK_OVERLAP
    assert settings.rag_top_k == rag_defaults.DEFAULT_RAG_TOP_K
    assert settings.rag_retrieval_candidates == rag_defaults.DEFAULT_RAG_RETRIEVAL_CANDIDATES
    assert settings.rag_rrf_constant == rag_defaults.DEFAULT_RAG_RRF_CONSTANT


def test_rag_settings_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    monkeypatch.setenv("RAG_DOCUMENT_DIR", "data/documents-test")
    monkeypatch.setenv("RAG_INDEX_DIR", "data/indexes-test")
    monkeypatch.setenv("RAG_CHUNK_SIZE", "512")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "64")
    monkeypatch.setenv("RAG_TOP_K", "8")
    get_settings.cache_clear()
    settings = Settings(_env_file=None)
    assert settings.ollama_base_url == "http://127.0.0.1:11435"
    assert settings.ollama_model == "mistral"
    assert settings.rag_document_dir == "data/documents-test"
    assert settings.rag_index_dir == "data/indexes-test"
    assert settings.rag_chunk_size == 512
    assert settings.rag_chunk_overlap == 64
    assert settings.rag_top_k == 8
    get_settings.cache_clear()


def test_rag_package_defaults_module_exports() -> None:
    assert rag_defaults.DEFAULT_RAG_CHUNK_OVERLAP < rag_defaults.DEFAULT_RAG_CHUNK_SIZE
    assert rag_defaults.DEFAULT_RAG_TOP_K >= 1


def test_rag_top_k_rejects_values_above_20() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_top_k=21)


def test_rag_retrieval_candidates_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_retrieval_candidates=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rag_retrieval_candidates=201)


def test_jwt_settings_defaults_and_overrides() -> None:
    settings = Settings(_env_file=None)
    assert settings.jwt_secret_key == ""
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_access_token_expire_minutes == 60

    settings = Settings(
        _env_file=None,
        jwt_secret_key="override-secret-32-characters-minimum",
        jwt_algorithm="HS256",
        jwt_access_token_expire_minutes=15,
    )
    assert settings.jwt_access_token_expire_minutes == 15


def test_jwt_algorithm_rejects_unsupported_values() -> None:
    with pytest.raises(ValidationError, match="HS256"):
        Settings(_env_file=None, jwt_algorithm="RS256")
