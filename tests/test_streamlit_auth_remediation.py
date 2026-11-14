"""Phase 6.5 — Streamlit security remediation tests."""

from __future__ import annotations

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from smarthris.schemas.auth import CurrentUserResponse
from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.auth import (
    ENV_PAGE_GUARD_TEST,
    ENV_PROFILE_REFRESH_SECONDS,
    MSG_SESSION_EXPIRED,
    SESSION_ACCESS_TOKEN,
    SESSION_AUTH_ERROR,
    SESSION_AUTH_PENDING_RERUN,
    SESSION_AUTHENTICATED,
    SESSION_CURRENT_USER,
    SESSION_TEST_BYPASS,
    apply_pending_login_rerun,
    clear_auth_session,
    current_role,
    enable_page_guard_bypass_for_tests,
    is_authenticated,
    maybe_refresh_authenticated_profile,
    page_guard_test_mode_enabled,
    require_page_access,
    set_auth_session,
)
from smarthris.models.user import UserRole

_TIMEOUT = 10
_TOKEN = "streamlit-test-token"
_PROFILE = {
    "id": 1,
    "email": "user@example.com",
    "role": "hr",
    "employee_id": None,
    "is_active": True,
    "last_login_at": None,
}


def test_page_guard_bypass_requires_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_PAGE_GUARD_TEST, raising=False)
    assert page_guard_test_mode_enabled() is False

    def script() -> None:
        enable_page_guard_bypass_for_tests()
        assert not __import__("streamlit").st.session_state.get(SESSION_TEST_BYPASS)

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()

    monkeypatch.setenv(ENV_PAGE_GUARD_TEST, "1")
    assert page_guard_test_mode_enabled() is True

    def script_ok() -> None:
        enable_page_guard_bypass_for_tests()
        assert __import__("streamlit").st.session_state.get(SESSION_TEST_BYPASS) is True

    AppTest.from_function(script_ok, default_timeout=_TIMEOUT).run()


def test_page_guard_bypass_blocked_in_production_even_with_test_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv(ENV_PAGE_GUARD_TEST, "1")
    assert page_guard_test_mode_enabled() is False

    def script() -> None:
        import os

        import streamlit as st

        from smarthris.schemas.auth import CurrentUserResponse
        from streamlit_app.auth import (
            SESSION_TEST_BYPASS,
            enable_page_guard_bypass_for_tests,
            require_page_access,
            set_auth_session,
        )
        from streamlit_app.constants import ENV_PAGE_GUARD_TEST

        os.environ["APP_ENV"] = "production"
        os.environ[ENV_PAGE_GUARD_TEST] = "1"
        enable_page_guard_bypass_for_tests()
        st.session_state[SESSION_TEST_BYPASS] = True
        set_auth_session(
            access_token="employee-test-token",
            expires_in=3600,
            user=CurrentUserResponse.model_validate(
                {
                    "id": 2,
                    "email": "emp@example.com",
                    "role": "employee",
                    "employee_id": 9,
                    "is_active": True,
                    "last_login_at": None,
                }
            ),
        )
        require_page_access("bulk_import")

    at = AppTest.from_function(script, default_timeout=_TIMEOUT).run()
    assert at.error
    assert "permission" in " ".join(str(item.value) for item in at.error).lower()


def test_logout_clears_page_guard_bypass() -> None:
    def script() -> None:
        import os

        import streamlit as st

        from streamlit_app.auth import (
            SESSION_TEST_BYPASS,
            clear_auth_session,
            enable_page_guard_bypass_for_tests,
        )
        from streamlit_app.constants import ENV_PAGE_GUARD_TEST

        os.environ[ENV_PAGE_GUARD_TEST] = "1"
        enable_page_guard_bypass_for_tests()
        assert st.session_state.get(SESSION_TEST_BYPASS) is True
        clear_auth_session()
        assert st.session_state.get(SESSION_TEST_BYPASS) is None

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_api_401_clears_session_and_schedules_login_rerun() -> None:
    def script() -> None:
        import streamlit as st

        from streamlit_app.auth import _handle_unauthorized, set_auth_session

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_PROFILE),
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Not authenticated"})

        client = SmartHrisApiClient(
            base_url="http://test.local",
            transport=httpx.MockTransport(handler),
            access_token=_TOKEN,
            on_unauthorized=_handle_unauthorized,
        )
        try:
            client.list_employees()
        except ApiError:
            pass
        assert not is_authenticated()
        assert st.session_state.get(SESSION_AUTH_PENDING_RERUN) is True
        assert MSG_SESSION_EXPIRED in str(st.session_state.get(SESSION_AUTH_ERROR))

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_api_403_does_not_clear_authenticated_session() -> None:
    def script() -> None:
        from streamlit_app.auth import _handle_unauthorized, set_auth_session

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_PROFILE),
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "forbidden"})

        client = SmartHrisApiClient(
            base_url="http://test.local",
            transport=httpx.MockTransport(handler),
            access_token=_TOKEN,
            on_unauthorized=_handle_unauthorized,
        )
        try:
            client.get_lms_summary()
            raise AssertionError("expected ApiError")
        except ApiError:
            pass
        assert is_authenticated()

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_profile_refresh_updates_role_after_demotion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROFILE_REFRESH_SECONDS, "0")

    def script() -> None:
        from unittest.mock import patch

        from smarthris.schemas.auth import CurrentUserResponse

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_PROFILE),
        )
        assert current_role() == UserRole.HR

        demoted = CurrentUserResponse.model_validate(
            {**_PROFILE, "role": "employee", "employee_id": 42}
        )

        with patch(
            "streamlit_app.auth.SmartHrisApiClient.get_current_user",
            return_value=demoted,
        ):
            maybe_refresh_authenticated_profile()

        assert current_role() == UserRole.EMPLOYEE
        assert __import__("streamlit").st.session_state[SESSION_CURRENT_USER]["role"] == "employee"

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_profile_refresh_updates_role_after_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROFILE_REFRESH_SECONDS, "0")

    def script() -> None:
        from unittest.mock import patch

        from smarthris.schemas.auth import CurrentUserResponse

        employee_profile = {**_PROFILE, "role": "employee", "employee_id": 42, "email": "emp@example.com"}
        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(employee_profile),
        )
        assert current_role() == UserRole.EMPLOYEE

        promoted = CurrentUserResponse.model_validate({**employee_profile, "role": "hr", "employee_id": None})

        with patch(
            "streamlit_app.auth.SmartHrisApiClient.get_current_user",
            return_value=promoted,
        ):
            maybe_refresh_authenticated_profile()

        assert current_role() == UserRole.HR
        assert __import__("streamlit").st.session_state[SESSION_CURRENT_USER]["role"] == "hr"

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_profile_refresh_inactive_clears_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROFILE_REFRESH_SECONDS, "0")

    def script() -> None:
        from unittest.mock import patch

        from smarthris.schemas.auth import CurrentUserResponse

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_PROFILE),
        )
        inactive = CurrentUserResponse.model_validate({**_PROFILE, "is_active": False})

        with patch(
            "streamlit_app.auth.SmartHrisApiClient.get_current_user",
            return_value=inactive,
        ):
            maybe_refresh_authenticated_profile()

        assert not is_authenticated()

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_profile_refresh_invalid_token_clears_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PROFILE_REFRESH_SECONDS, "0")

    def script() -> None:
        import streamlit as st

        from streamlit_app.auth import _handle_unauthorized

        set_auth_session(
            access_token=_TOKEN,
            expires_in=3600,
            user=CurrentUserResponse.model_validate(_PROFILE),
        )

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "Not authenticated"})

        with __import__("unittest.mock").patch(
            "streamlit_app.auth.SmartHrisApiClient",
            lambda **kw: SmartHrisApiClient(
                base_url="http://t",
                transport=httpx.MockTransport(handler),
                access_token=_TOKEN,
                on_unauthorized=_handle_unauthorized,
            ),
        ):
            maybe_refresh_authenticated_profile()

        assert not is_authenticated()
        assert st.session_state.get(SESSION_AUTH_PENDING_RERUN) is True

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()


def test_apply_pending_login_rerun_consumes_flag() -> None:
    def script() -> None:
        import streamlit as st

        st.session_state[SESSION_AUTH_PENDING_RERUN] = True
        # AppTest cannot assert st.rerun() was called; ensure flag is consumed.
        try:
            apply_pending_login_rerun()
        except Exception:
            pass
        assert SESSION_AUTH_PENDING_RERUN not in st.session_state or not st.session_state.get(
            SESSION_AUTH_PENDING_RERUN
        )

    AppTest.from_function(script, default_timeout=_TIMEOUT).run()