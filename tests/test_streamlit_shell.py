"""Smoke checks for Streamlit shell modules (no Streamlit server)."""

from __future__ import annotations

from pathlib import Path


def test_streamlit_app_layout_exists() -> None:
    root = Path(__file__).resolve().parents[1] / "streamlit_app"
    assert (root / "app.py").is_file()
    assert (root / "api_client.py").is_file()
    assert (root / "constants.py").is_file()
    pages = root / "pages"
    assert (pages / "dashboard.py").is_file()
    assert (pages / "employees.py").is_file()
    assert (pages / "bulk_import.py").is_file()
    assert (pages / "lms_analytics.py").is_file()
    assert (pages / "hr_document_qa.py").is_file()


def test_app_entrypoint_mentions_expected_startup() -> None:
    text = (Path(__file__).resolve().parents[1] / "streamlit_app" / "app.py").read_text(
        encoding="utf-8"
    )
    assert "streamlit run streamlit_app/app.py" in text
    assert "NAV_DASHBOARD" in text
    assert "NAV_EMPLOYEES" in text
    assert "NAV_BULK_IMPORT" in text
    assert "NAV_LMS_ANALYTICS" in text
    assert "NAV_HR_DOCUMENT_QA" in text
    assert "hr_document_qa.py" in text
