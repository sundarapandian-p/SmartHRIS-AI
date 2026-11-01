"""Dashboard landing page — API connectivity only (no business workflows)."""

from __future__ import annotations

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import APP_TAGLINE, APP_TITLE, NAV_DASHBOARD


def render() -> None:
    st.title(NAV_DASHBOARD)
    st.subheader(APP_TITLE)
    st.write(APP_TAGLINE)
    st.caption(
        "This UI is a thin client. Start the FastAPI backend separately, then use "
        "the pages in the sidebar."
    )

    st.divider()
    st.markdown("### API connectivity")
    client = SmartHrisApiClient()
    st.code(f"API_BASE_URL = {client.base_url}", language="text")

    if st.button("Check API health", type="primary"):
        try:
            health = client.get_health()
        except ApiError as exc:
            st.error(str(exc))
        else:
            st.success("API responded.")
            st.json(health)


render()
