"""Phase 6.3 — Streamlit authentication, navigation, and API client auth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from smarthris.models.user import UserRole
from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.auth import (
    SESSION_ACCESS_TOKEN,
    SESSION_AUTHENTICATED,
    SESSION_CURRENT_USER,
    SESSION_AUTH_MESSAGE,
    allowed_nav_titles,
    clear_auth_session,
    is_authenticated,
    nav_page_specs,
    set_auth_session,
)
from streamlit_app.constants import (
    NAV_BULK_IMPORT,
    NAV_DASHBOARD,
    NAV_EMPLOYEES,
    NAV_HR_DOCUMENT_QA,
    NAV_LMS_ANALYTICS,
    NAV_MY_PROFILE,
)
from smarthris.schemas.auth import CurrentUserResponse

_TIMEOUT = 10
_TOKEN = "test.jwt.token.value"
_ME = {
    "id": 1,
    "email": "hr@example.com",
    "role": "hr",
    "employee_id": None,
    "is_active": True,
    "last_login_at": None,
}


def _login_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/auth/login" and request.method == "POST":
        auth = request.headers.get("authorization")
        assert auth is None
        return httpx.Response(
            200,
            json={"access_token": _TOKEN, "token_type": "bearer", "expires_in": 3600},
        )
    if request.url.path == "/auth/me":
        assert request.headers.get("authorization") == f"Bearer {_TOKEN}"
        return httpx.Response(200, json=_ME)
    return httpx.Response(404, json={"detail": "not found"})


def test_allowed_nav_titles_by_role() -> None:
    admin = allowed_nav_titles(UserRole.ADMIN)
    assert NAV_DASHBOARD in admin
    assert NAV_EMPLOYEES in admin
    assert NAV_BULK_IMPORT in admin
    assert NAV_LMS_ANALYTICS in admin
    assert NAV_HR_DOCUMENT_QA in admin
    assert NAV_MY_PROFILE not in admin

    employee = allowed_nav_titles(UserRole.EMPLOYEE)
    assert employee == frozenset({NAV_DASHBOARD, NAV_MY_PROFILE, NAV_HR_DOCUMENT_QA})
    assert NAV_BULK_IMPORT not in employee


def _pages_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "streamlit_app" / "pages"


def test_nav_page_specs_admin_hr_employee_order() -> None:
    pages_dir = _pages_dir()
    admin_specs = nav_page_specs(allowed_nav_titles(UserRole.ADMIN), pages_dir)
    assert admin_specs[0][0] == "dashboard"
    assert [s[0] for s in admin_specs] == [
        "dashboard",
        "employees",
        "bulk_import",
        "lms_analytics",
        "hr_document_qa",
    ]

    hr_specs = nav_page_specs(allowed_nav_titles(UserRole.HR), pages_dir)
    assert [s[0] for s in hr_specs] == [s[0] for s in admin_specs]

    employee_specs = nav_page_specs(allowed_nav_titles(UserRole.EMPLOYEE), pages_dir)
    assert employee_specs[0][0] == "dashboard"
    assert [s[0] for s in employee_specs] == [
        "dashboard",
        "my_profile",
        "hr_document_qa",
    ]


def test_navigation_page_default_flags_for_roles() -> None:
    pages_dir = _pages_dir()

    def default_flags(role: UserRole) -> list[bool]:
        specs = nav_page_specs(allowed_nav_titles(role), pages_dir)
        return [(index == 0) for index in range(len(specs))]

    for role in (UserRole.ADMIN, UserRole.HR, UserRole.EMPLOYEE):
        flags = default_flags(role)
        assert flags, f"expected at least one page for {role}"
        assert flags.count(True) == 1
        assert flags[0] is True


def test_api_client_login_without_bearer() -> None:
    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(_login_handler),
    )
    token = client.login("hr@example.com", "Secret1!")
    assert token.access_token == _TOKEN
    client.set_access_token(token.access_token)
    profile = client.get_current_user()
    assert profile.email == "hr@example.com"


def test_api_client_sends_bearer_on_protected_route() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        if request.url.path == "/employees":
            return httpx.Response(
                200,
                json={
                    "items": [],
                    "total": 0,
                    "page": 1,
                    "page_size": 20,
                    "pages": 0,
                },
            )
        return httpx.Response(404)

    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
        access_token=_TOKEN,
    )
    client.list_employees()
    assert seen == [f"Bearer {_TOKEN}"]


def test_api_client_401_invokes_callback_not_on_403() -> None:
    calls: list[str] = []

    def on_401() -> None:
        calls.append("logout")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/employees":
            return httpx.Response(401, json={"detail": "Not authenticated"})
        if request.url.path == "/analytics/lms/summary":
            return httpx.Response(403, json={"detail": "forbidden"})
        return httpx.Response(404)

    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
        access_token=_TOKEN,
        on_unauthorized=on_401,
    )
    with pytest.raises(ApiError) as exc401:
        client.list_employees()
    assert exc401.value.status_code == 401
    assert calls == ["logout"]

    calls.clear()
    with pytest.raises(ApiError) as exc403:
        client.get_lms_summary()
    assert exc403.value.status_code == 403
    assert calls == []


def test_api_error_message_never_includes_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad token"})

    client = SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
        access_token=_TOKEN,
    )
    with pytest.raises(ApiError) as exc_info:
        client.list_employees()
    assert _TOKEN not in str(exc_info.value)


def test_logout_clears_authentication_state() -> None:
    def script() -> None:
        import streamlit as st

        from streamlit_app.auth import (
            SESSION_ACCESS_TOKEN,
            SESSION_AUTHENTICATED,
            clear_auth_session,
            is_authenticated,
            set_auth_session,
        )

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_ME),
        )
        clear_auth_session()
        assert not is_authenticated()
        assert SESSION_ACCESS_TOKEN not in st.session_state or st.session_state.get(
            SESSION_ACCESS_TOKEN
        ) is None
        assert st.session_state.get(SESSION_AUTHENTICATED) is False

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_set_and_clear_auth_session_key_behavior() -> None:
    def script() -> None:
        import streamlit as st

        from streamlit_app.auth import (
            SESSION_AUTHENTICATED,
            SESSION_ACCESS_TOKEN,
            SESSION_CURRENT_USER,
            clear_auth_session,
            is_authenticated,
            set_auth_session,
        )

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_ME),
        )
        assert is_authenticated()
        assert st.session_state[SESSION_ACCESS_TOKEN] == _TOKEN
        assert st.session_state[SESSION_AUTHENTICATED] is True
        assert st.session_state[SESSION_CURRENT_USER]["email"] == "hr@example.com"
        clear_auth_session(message="signed out")
        assert not is_authenticated()
        assert st.session_state.get(SESSION_ACCESS_TOKEN) is None

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_bulk_import_page_guard_blocks_employee() -> None:
    def script() -> None:
        from streamlit_app.auth import require_page_access, set_auth_session
        from smarthris.schemas.auth import CurrentUserResponse

        set_auth_session(
            access_token="employee-test-token",
            expires_in=3600,
            user=CurrentUserResponse.model_validate(
                {
                    "id": 2,
                    "email": "emp@example.com",
                    "role": "employee",
                    "employee_id": 5,
                    "is_active": True,
                    "last_login_at": None,
                }
            ),
        )
        require_page_access("bulk_import")

    at = AppTest.from_function(script, default_timeout=_TIMEOUT).run()
    assert at.error
    assert "permission" in " ".join(str(item.value) for item in at.error).lower()


def test_my_profile_rejects_missing_link() -> None:
    def script() -> None:
        from streamlit_app.auth import set_auth_session
        from streamlit_app.pages.my_profile import render
        from smarthris.schemas.auth import CurrentUserResponse

        set_auth_session(
            access_token="employee-test-token",
            expires_in=3600,
            user=CurrentUserResponse.model_validate(
                {
                    "id": 3,
                    "email": "nolink@example.com",
                    "role": "employee",
                    "employee_id": None,
                    "is_active": True,
                    "last_login_at": None,
                }
            ),
        )
        render()

    at = AppTest.from_function(script, default_timeout=_TIMEOUT).run()
    assert at.error
    assert "linked" in " ".join(str(item.value) for item in at.error).lower()