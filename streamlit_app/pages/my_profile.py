"""Employee self-service profile (own record only)."""

from __future__ import annotations

from typing import Any

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.auth import (
    get_api_client,
    linked_employee_id,
    require_page_access,
)
from streamlit_app.constants import NAV_MY_PROFILE


def employee_to_row(employee: dict[str, Any]) -> dict[str, Any]:
    return {
        "Employee number": employee.get("employee_number"),
        "First name": employee.get("first_name"),
        "Last name": employee.get("last_name"),
        "Work email": employee.get("work_email"),
        "Department ID": employee.get("department_id"),
        "Job title": employee.get("job_title"),
        "Status": employee.get("status"),
        "Hire date": employee.get("hire_date"),
    }


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    require_page_access("my_profile")
    st.title(NAV_MY_PROFILE)

    employee_id = linked_employee_id()
    if employee_id is None:
        st.error(
            "Your account is not linked to an employee record. "
            "Contact HR to complete your profile setup."
        )
        return

    client = api_client if api_client is not None else get_api_client()
    try:
        record = client.get_employee(employee_id)
    except ApiError as exc:
        if exc.status_code == 403:
            st.error("You do not have permission to view this profile.")
        elif exc.status_code == 404:
            st.error("Your employee record could not be found.")
        else:
            st.error(str(exc))
        return

    st.success("This is your employee profile (read-only).")
    st.dataframe([employee_to_row(record.model_dump(mode="json"))], width="stretch", hide_index=True)


if __name__ == "__main__":
    render()