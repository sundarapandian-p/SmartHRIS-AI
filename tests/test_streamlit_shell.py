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
    assert (pages / "login.py").is_file()
    assert (pages / "my_profile.py").is_file()
    assert (root / "auth.py").is_file()


def test_app_entrypoint_mentions_expected_startup() -> None:
    text = (Path(__file__).resolve().parents[1] / "streamlit_app" / "app.py").read_text(
        encoding="utf-8"
    )
    assert "streamlit run streamlit_app/app.py" in text
    assert "allowed_nav_titles" in text
    assert "nav_page_specs" in text
    assert "is_authenticated" in text
    assert "login.py" in text
    assert "nav_page_specs" in text
    assert "default=(index == 0)" in text
    assert ".default" not in text.replace("default=True", "").replace("default=False", "").replace(
        "default=(index == 0)", ""
    )
