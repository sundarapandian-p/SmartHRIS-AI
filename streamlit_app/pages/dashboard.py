"""Dashboard landing page — API connectivity only (no business workflows)."""

from __future__ import annotations

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.auth import (
    current_user,
    get_api_client,
    is_hr_or_admin,
    linked_employee_id,
    require_page_access,
)
from streamlit_app.constants import APP_TAGLINE, APP_TITLE, NAV_DASHBOARD


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    require_page_access("dashboard")
    st.title(NAV_DASHBOARD)
    st.subheader(APP_TITLE)

    user = current_user()
    if user and not is_hr_or_admin():
        st.write(
            "Welcome. Use **My Profile** for your employee details and "
            "**HR Document Q&A** for policies shared with all staff."
        )
    else:
        st.write(APP_TAGLINE)
        st.caption(
            "This UI is a thin client. Start the FastAPI backend separately, then use "
            "the pages in the sidebar."
        )

    st.divider()
    if user and not is_hr_or_admin():
        employee_id = linked_employee_id()
        client = api_client if api_client is not None else get_api_client()
        if employee_id is not None:
            st.markdown("### Your profile summary")
            try:
                record = client.get_employee(employee_id)
            except ApiError as exc:
                st.warning(f"Could not load your profile: {exc}")
            else:
                st.write(
                    f"**{record.first_name} {record.last_name}** "
                    f"({record.employee_number}) — {record.work_email}"
                )
        else:
            st.info("Your login is not linked to an employee record yet.")

    st.markdown("### API connectivity")
    client = api_client if api_client is not None else get_api_client()
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
