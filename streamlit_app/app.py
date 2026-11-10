"""SmartHRIS-AI Streamlit entrypoint.

Run from the repository root::

    python -m streamlit run streamlit_app/app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from streamlit_app.constants import (
    APP_TITLE,
    NAV_BULK_IMPORT,
    NAV_DASHBOARD,
    NAV_EMPLOYEES,
    NAV_LMS_ANALYTICS,
)

PAGES_DIR = Path(__file__).resolve().parent / "pages"

st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
    initial_sidebar_state="expanded",
)

dashboard_page = st.Page(
    PAGES_DIR / "dashboard.py",
    title=NAV_DASHBOARD,
    default=True,
)
employees_page = st.Page(
    PAGES_DIR / "employees.py",
    title=NAV_EMPLOYEES,
)
bulk_import_page = st.Page(
    PAGES_DIR / "bulk_import.py",
    title=NAV_BULK_IMPORT,
)
lms_analytics_page = st.Page(
    PAGES_DIR / "lms_analytics.py",
    title=NAV_LMS_ANALYTICS,
)

navigation = st.navigation(
    [dashboard_page, employees_page, bulk_import_page, lms_analytics_page],
    position="sidebar",
)
navigation.run()
