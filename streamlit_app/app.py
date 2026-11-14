"""SmartHRIS-AI Streamlit entrypoint.

Run from the repository root::

    python -m streamlit run streamlit_app/app.py
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from streamlit_app.auth import (
    allowed_nav_titles,
    apply_pending_login_rerun,
    current_role,
    is_authenticated,
    maybe_refresh_authenticated_profile,
    nav_page_specs,
    render_sidebar_user_block,
)
from streamlit_app.constants import (
    APP_TITLE,
    NAV_LOGIN,
)

PAGES_DIR = Path(__file__).resolve().parent / "pages"

st.set_page_config(
    page_title=APP_TITLE,
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_pending_login_rerun()

if not is_authenticated():
    login_page = st.Page(
        PAGES_DIR / "login.py",
        title=NAV_LOGIN,
        default=True,
    )
    st.navigation([login_page], position="sidebar").run()
else:
    maybe_refresh_authenticated_profile()
    apply_pending_login_rerun()
    if not is_authenticated():
        login_page = st.Page(
            PAGES_DIR / "login.py",
            title=NAV_LOGIN,
            default=True,
        )
        st.navigation([login_page], position="sidebar").run()
        st.stop()
    render_sidebar_user_block()
    role = current_role()
    allowed = allowed_nav_titles(role)

    specs = nav_page_specs(allowed, PAGES_DIR)
    if not specs:
        st.error("No pages are available for your role.")
        st.stop()

    all_pages: list[tuple[str, st.Page]] = [
        (
            module_key,
            st.Page(
                script_path,
                title=title,
                default=(index == 0),
            ),
        )
        for index, (module_key, script_path, title) in enumerate(specs)
    ]

    st.navigation([page for _, page in all_pages], position="sidebar").run()