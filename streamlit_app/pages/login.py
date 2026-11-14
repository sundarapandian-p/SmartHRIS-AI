"""Login page — POST /auth/login then GET /auth/me."""

from __future__ import annotations

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.auth import (
    SESSION_AUTH_ERROR,
    clear_auth_session,
    is_authenticated,
    pop_auth_message,
    set_auth_session,
)
from streamlit_app.constants import APP_TITLE, NAV_LOGIN

_GENERIC_LOGIN_FAILURE = "Invalid email or password."
_SERVER_UNAVAILABLE = (
    "Cannot reach the API. Confirm the backend is running and API_BASE_URL is correct."
)
_CONFIG_ERROR = "The sign-in service is temporarily unavailable. Contact an administrator."


def render() -> None:
    if is_authenticated():
        st.info("You are already signed in. Use the sidebar to open another page.")
        return

    st.title(NAV_LOGIN)
    st.subheader(APP_TITLE)

    banner = pop_auth_message() or st.session_state.get(SESSION_AUTH_ERROR)
    if banner:
        st.warning(str(banner))

    with st.form("login_form", clear_on_submit=True):
        email = st.text_input("Email", autocomplete="username")
        password = st.text_input("Password", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Sign in", type="primary")

    if not submitted:
        return

    email_value = (email or "").strip()
    if not email_value or not password:
        st.error("Enter your email and password.")
        return

    client = SmartHrisApiClient()
    try:
        token = client.login(email_value, password)
    except ApiError as exc:
        if exc.status_code in {401, 400}:
            st.error(_GENERIC_LOGIN_FAILURE)
        elif exc.status_code == 503 and exc.code == "jwt_misconfigured":
            st.error(_CONFIG_ERROR)
        elif exc.status_code is None:
            st.error(_SERVER_UNAVAILABLE)
        else:
            st.error(_GENERIC_LOGIN_FAILURE)
        return

    client.set_access_token(token.access_token)
    try:
        profile = client.get_current_user()
    except ApiError as exc:
        clear_auth_session()
        if exc.status_code == 401:
            st.error(_GENERIC_LOGIN_FAILURE)
        elif exc.status_code is None:
            st.error(_SERVER_UNAVAILABLE)
        else:
            st.error("Sign-in succeeded but your profile could not be loaded. Try again.")
        return

    set_auth_session(
        access_token=token.access_token,
        expires_in=token.expires_in,
        user=profile,
    )
    st.session_state.pop(SESSION_AUTH_ERROR, None)
    st.rerun()


if __name__ == "__main__":
    render()