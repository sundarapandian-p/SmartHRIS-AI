"""Shared UI labels for the Streamlit shell (safe to import in tests)."""

APP_TITLE = "SmartHRIS-AI"
APP_TAGLINE = "HRIS learning project — employees, bulk import, LMS analytics, and more."

NAV_LOGIN = "Login"
NAV_DASHBOARD = "Dashboard"
NAV_EMPLOYEES = "Employees"
NAV_MY_PROFILE = "My Profile"
NAV_BULK_IMPORT = "Bulk Import"
NAV_LMS_ANALYTICS = "LMS Analytics"
NAV_HR_DOCUMENT_QA = "HR Document Q&A"

NAV_ITEMS = (
    NAV_DASHBOARD,
    NAV_EMPLOYEES,
    NAV_BULK_IMPORT,
    NAV_LMS_ANALYTICS,
    NAV_HR_DOCUMENT_QA,
)

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"

# Streamlit security / test hooks (see streamlit_app/auth.py).
ENV_PAGE_GUARD_TEST = "SMART_HRIS_STREAMLIT_PAGE_GUARD_TEST"
ENV_PROFILE_REFRESH_SECONDS = "SMART_HRIS_STREAMLIT_PROFILE_REFRESH_SECONDS"
