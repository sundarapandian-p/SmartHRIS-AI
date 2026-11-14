"""Streamlit session authentication (JWT); backend remains authority."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

from smarthris.models.user import UserRole
from smarthris.schemas.auth import CurrentUserResponse
from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import (
    ENV_PAGE_GUARD_TEST,
    ENV_PROFILE_REFRESH_SECONDS,
    NAV_BULK_IMPORT,
    NAV_DASHBOARD,
    NAV_EMPLOYEES,
    NAV_HR_DOCUMENT_QA,
    NAV_LMS_ANALYTICS,
    NAV_LOGIN,
    NAV_MY_PROFILE,
)

SESSION_ACCESS_TOKEN = "auth_access_token"
SESSION_CURRENT_USER = "auth_current_user"
SESSION_AUTHENTICATED = "auth_authenticated"
SESSION_TOKEN_EXPIRES_AT = "auth_token_expires_at"
SESSION_AUTH_MESSAGE = "auth_message"
SESSION_AUTH_ERROR = "auth_auth_error"
SESSION_TEST_BYPASS = "auth_test_bypass"
SESSION_AUTH_PENDING_RERUN = "auth_pending_login_rerun"
SESSION_PROFILE_LAST_REFRESH = "auth_profile_last_refresh"

MSG_SESSION_EXPIRED = "Your session has expired. Please sign in again."
MSG_ACCOUNT_INACTIVE = "Your account is no longer active. Please contact an administrator."
MSG_ACCESS_DENIED = "You do not have permission to view this page."

_DEFAULT_PROFILE_REFRESH_SECONDS = 30


def _app_env() -> str:
    return os.environ.get("APP_ENV", "development").strip().lower()


def page_guard_test_mode_enabled() -> bool:
    """True only when explicit test opt-in is set and app is not staging/production."""
    if _app_env() in {"production", "staging"}:
        return False
    return os.environ.get(ENV_PAGE_GUARD_TEST, "").strip() == "1"


def _test_bypass_active() -> bool:
    if not page_guard_test_mode_enabled():
        return False
    return bool(st.session_state.get(SESSION_TEST_BYPASS))


def _profile_refresh_interval_seconds() -> int:
    raw = os.environ.get(ENV_PROFILE_REFRESH_SECONDS, "").strip()
    if raw.isdigit():
        return max(0, int(raw))
    return _DEFAULT_PROFILE_REFRESH_SECONDS


def _init_defaults() -> None:
    if SESSION_AUTHENTICATED not in st.session_state:
        st.session_state[SESSION_AUTHENTICATED] = False


def is_authenticated() -> bool:
    _init_defaults()
    token = st.session_state.get(SESSION_ACCESS_TOKEN)
    if not token or not st.session_state.get(SESSION_AUTHENTICATED):
        return False
    expires_at = st.session_state.get(SESSION_TOKEN_EXPIRES_AT)
    if isinstance(expires_at, (int, float)) and time.time() >= float(expires_at):
        clear_auth_session(message=MSG_SESSION_EXPIRED)
        return False
    return True


def get_access_token() -> str | None:
    if not is_authenticated():
        return None
    raw = st.session_state.get(SESSION_ACCESS_TOKEN)
    return str(raw) if raw else None


def current_user() -> dict[str, Any] | None:
    if not is_authenticated():
        return None
    payload = st.session_state.get(SESSION_CURRENT_USER)
    return payload if isinstance(payload, dict) else None


def current_role() -> UserRole | None:
    user = current_user()
    if not user:
        return None
    raw = user.get("role")
    if raw is None:
        return None
    try:
        return UserRole(str(raw))
    except ValueError:
        return None


def is_hr_or_admin() -> bool:
    role = current_role()
    return role in {UserRole.ADMIN, UserRole.HR}


def linked_employee_id() -> int | None:
    user = current_user()
    if not user:
        return None
    employee_id = user.get("employee_id")
    return int(employee_id) if employee_id is not None else None


def set_auth_session(
    *,
    access_token: str,
    expires_in: int,
    user: CurrentUserResponse | dict[str, Any],
) -> None:
    profile = (
        user.model_dump(mode="json")
        if isinstance(user, CurrentUserResponse)
        else dict(user)
    )
    st.session_state[SESSION_ACCESS_TOKEN] = access_token
    st.session_state[SESSION_CURRENT_USER] = profile
    st.session_state[SESSION_AUTHENTICATED] = True
    st.session_state[SESSION_TOKEN_EXPIRES_AT] = time.time() + int(expires_in)
    st.session_state[SESSION_AUTH_MESSAGE] = None
    st.session_state[SESSION_AUTH_ERROR] = None
    st.session_state[SESSION_PROFILE_LAST_REFRESH] = time.time()


def clear_auth_session(*, message: str | None = None) -> None:
    for key in (
        SESSION_ACCESS_TOKEN,
        SESSION_CURRENT_USER,
        SESSION_TOKEN_EXPIRES_AT,
        SESSION_TEST_BYPASS,
        SESSION_PROFILE_LAST_REFRESH,
    ):
        st.session_state.pop(key, None)
    st.session_state[SESSION_AUTHENTICATED] = False
    if message:
        st.session_state[SESSION_AUTH_MESSAGE] = message


def pop_auth_message() -> str | None:
    msg = st.session_state.pop(SESSION_AUTH_MESSAGE, None)
    return str(msg) if msg else None


def request_login_rerun() -> None:
    """Schedule a single Streamlit rerun to land on the login shell."""
    st.session_state[SESSION_AUTH_PENDING_RERUN] = True


def apply_pending_login_rerun() -> None:
    """Consume a pending login rerun once (avoids tight loops)."""
    if st.session_state.pop(SESSION_AUTH_PENDING_RERUN, False):
        st.rerun()


def _handle_unauthorized() -> None:
    clear_auth_session(message=MSG_SESSION_EXPIRED)
    st.session_state[SESSION_AUTH_ERROR] = MSG_SESSION_EXPIRED
    request_login_rerun()


def get_api_client(*, base_url: str | None = None) -> SmartHrisApiClient:
    client = SmartHrisApiClient(
        base_url=base_url,
        access_token=get_access_token(),
        on_unauthorized=_handle_unauthorized,
    )
    return client


def maybe_refresh_authenticated_profile() -> None:
    """Sync role/profile from GET /auth/me (throttled; app entry only)."""
    if not is_authenticated():
        return
    interval = _profile_refresh_interval_seconds()
    last = float(st.session_state.get(SESSION_PROFILE_LAST_REFRESH) or 0)
    if interval > 0 and (time.time() - last) < interval:
        return

    token = get_access_token()
    if not token:
        return

    client = SmartHrisApiClient(
        access_token=token,
        on_unauthorized=_handle_unauthorized,
    )
    try:
        profile = client.get_current_user()
    except ApiError as exc:
        if exc.status_code == 403:
            clear_auth_session(message=MSG_ACCOUNT_INACTIVE)
            request_login_rerun()
        return

    if not profile.is_active:
        clear_auth_session(message=MSG_ACCOUNT_INACTIVE)
        request_login_rerun()
        return

    st.session_state[SESSION_CURRENT_USER] = profile.model_dump(mode="json")
    st.session_state[SESSION_PROFILE_LAST_REFRESH] = time.time()


def allowed_nav_titles(role: UserRole | None) -> frozenset[str]:
    if role is None:
        return frozenset({NAV_LOGIN})
    common = {NAV_DASHBOARD, NAV_HR_DOCUMENT_QA}
    if role in {UserRole.ADMIN, UserRole.HR}:
        return frozenset(
            common
            | {
                NAV_EMPLOYEES,
                NAV_BULK_IMPORT,
                NAV_LMS_ANALYTICS,
            }
        )
    if role == UserRole.EMPLOYEE:
        return frozenset(common | {NAV_MY_PROFILE})
    return frozenset()


_NAV_PAGE_ORDER: tuple[tuple[str, str, str], ...] = (
    (NAV_DASHBOARD, "dashboard", "dashboard.py"),
    (NAV_MY_PROFILE, "my_profile", "my_profile.py"),
    (NAV_EMPLOYEES, "employees", "employees.py"),
    (NAV_BULK_IMPORT, "bulk_import", "bulk_import.py"),
    (NAV_LMS_ANALYTICS, "lms_analytics", "lms_analytics.py"),
    (NAV_HR_DOCUMENT_QA, "hr_document_qa", "hr_document_qa.py"),
)


def nav_page_specs(
    allowed: frozenset[str],
    pages_dir: Path,
) -> list[tuple[str, Path, str]]:
    """Sidebar page order for allowed nav titles: (module_key, script_path, title)."""
    specs: list[tuple[str, Path, str]] = []
    for title, module_key, filename in _NAV_PAGE_ORDER:
        if title in allowed:
            specs.append((module_key, pages_dir / filename, title))
    return specs


def page_key_for_path(path: str) -> str:
    name = path.rsplit("/", 1)[-1].replace(".py", "")
    return name


_PAGE_ACCESS: dict[str, Callable[[], bool]] = {
    "dashboard": lambda: is_authenticated(),
    "employees": lambda: is_hr_or_admin(),
    "my_profile": lambda: current_role() == UserRole.EMPLOYEE and is_authenticated(),
    "bulk_import": lambda: is_hr_or_admin(),
    "lms_analytics": lambda: is_hr_or_admin(),
    "hr_document_qa": lambda: is_authenticated(),
    "login": lambda: True,
}


def enable_page_guard_bypass_for_tests() -> None:
    """Opt-in page guard bypass for Streamlit AppTest (never honored in production)."""
    if not page_guard_test_mode_enabled():
        return
    st.session_state[SESSION_TEST_BYPASS] = True


def require_page_access(module_name: str) -> None:
    """Stop page render when user lacks access (direct URL navigation)."""
    if _test_bypass_active():
        return
    check = _PAGE_ACCESS.get(module_name)
    if check is None:
        st.error(MSG_ACCESS_DENIED)
        st.stop()
    if not check():
        if not is_authenticated():
            st.warning("Please sign in to continue.")
            st.stop()
        st.error(MSG_ACCESS_DENIED)
        st.stop()


def render_sidebar_user_block() -> None:
    user = current_user()
    if not user:
        return
    with st.sidebar:
        st.caption(f"Signed in as **{user.get('email', 'user')}** ({user.get('role', '')})")
        if st.button("Log out", key="auth_logout_btn"):
            clear_auth_session()
            st.rerun()