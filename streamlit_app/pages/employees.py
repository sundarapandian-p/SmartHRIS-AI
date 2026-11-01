"""Employees page placeholder — listing UI comes in a later phase."""

from __future__ import annotations

import streamlit as st

from streamlit_app.constants import NAV_EMPLOYEES


def render() -> None:
    st.title(NAV_EMPLOYEES)
    st.info(
        "Employee listing and search will call `GET /employees` in a later phase. "
        "No employee workflow is implemented in this foundation shell."
    )
    st.caption("Backend API routes already exist; this page does not call them yet.")


render()
