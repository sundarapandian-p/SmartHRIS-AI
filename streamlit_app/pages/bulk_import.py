"""Bulk Import page placeholder — upload workflow comes in a later phase."""

from __future__ import annotations

import streamlit as st

from streamlit_app.constants import NAV_BULK_IMPORT


def render() -> None:
    st.title(NAV_BULK_IMPORT)
    st.info(
        "CSV bulk upload, job status polling, error review, and retry will be added "
        "in a later phase. Excel upload is out of scope for this shell."
    )
    st.caption(
        "Backend routes (`POST /imports`, job status, errors, retry) already exist; "
        "this page does not call them yet."
    )


render()
