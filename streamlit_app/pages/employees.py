"""Employees page — list/search/verify via GET /employees."""

from __future__ import annotations

from typing import Any

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import NAV_EMPLOYEES

# Backend EmployeeStatus values.
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_FILTER_ALL = "all"

DEFAULT_PAGE_SIZE = 20

SESSION_PAGE = "employees_page"
SESSION_PAGE_SIZE = "employees_page_size"
SESSION_FILTERS = "employees_filters"
SESSION_SNAPSHOT = "employees_snapshot"
SESSION_ERROR = "employees_error"
SESSION_LAST_QUERY = "employees_last_query"


def empty_filters() -> dict[str, Any]:
    return {
        "employee_number": None,
        "name": None,
        "work_email": None,
        "department_id": None,
        "status": None,
    }


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_department_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1 else None


def normalize_status_filter(value: str | None) -> str | None:
    if value is None or value == STATUS_FILTER_ALL:
        return None
    if value in {STATUS_ACTIVE, STATUS_INACTIVE}:
        return value
    return None


def build_list_query(
    *,
    page: int,
    page_size: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    """Build kwargs for ``SmartHrisApiClient.list_employees`` (actual API names)."""
    return {
        "page": int(page),
        "page_size": int(page_size),
        "employee_number": normalize_optional_text(filters.get("employee_number")),
        "name": normalize_optional_text(filters.get("name")),
        "work_email": normalize_optional_text(filters.get("work_email")),
        "department_id": normalize_department_id(filters.get("department_id")),
        "status": normalize_status_filter(filters.get("status")),
    }


def query_fingerprint(query: dict[str, Any]) -> tuple[Any, ...]:
    return (
        query.get("page"),
        query.get("page_size"),
        query.get("employee_number"),
        query.get("name"),
        query.get("work_email"),
        query.get("department_id"),
        query.get("status"),
    )


def filters_fingerprint(filters: dict[str, Any]) -> tuple[Any, ...]:
    return (
        normalize_optional_text(filters.get("employee_number")),
        normalize_optional_text(filters.get("name")),
        normalize_optional_text(filters.get("work_email")),
        normalize_department_id(filters.get("department_id")),
        normalize_status_filter(filters.get("status")),
    )


def employees_to_table_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Table columns from EmployeeRead fields that exist on the API.

    Department name is not returned by GET /employees; show ``department_id``.
    """
    rows: list[dict[str, Any]] = []
    for item in items:
        status = item.get("status")
        status_value = getattr(status, "value", status)
        rows.append(
            {
                "employee_number": item.get("employee_number"),
                "first_name": item.get("first_name"),
                "last_name": item.get("last_name"),
                "work_email": item.get("work_email"),
                "department_id": item.get("department_id"),
                "job_title": item.get("job_title"),
                "status": status_value,
            }
        )
    return rows


def fetch_and_store_employees(
    client: SmartHrisApiClient,
    query: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        result = client.list_employees(**query)
        payload = result.model_dump(mode="json")
        st.session_state[SESSION_SNAPSHOT] = payload
        st.session_state[SESSION_ERROR] = None
        st.session_state[SESSION_LAST_QUERY] = query_fingerprint(query)
        st.session_state[SESSION_PAGE] = int(query["page"])
        st.session_state[SESSION_PAGE_SIZE] = int(query["page_size"])
        st.session_state[SESSION_FILTERS] = {
            "employee_number": query.get("employee_number"),
            "name": query.get("name"),
            "work_email": query.get("work_email"),
            "department_id": query.get("department_id"),
            "status": query.get("status") or STATUS_FILTER_ALL,
        }
        return payload, None
    except ApiError as exc:
        st.session_state[SESSION_ERROR] = str(exc)
        return st.session_state.get(SESSION_SNAPSHOT), str(exc)


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    """Employee listing / verification (read-only)."""
    st.title(NAV_EMPLOYEES)
    st.caption(
        "Search and verify employees from the API. "
        "Create/edit/delete are not available on this page. "
        "Department is shown as ``department_id`` (no department list endpoint)."
    )

    if SESSION_FILTERS not in st.session_state:
        st.session_state[SESSION_FILTERS] = empty_filters() | {"status": STATUS_FILTER_ALL}
    if SESSION_PAGE not in st.session_state:
        st.session_state[SESSION_PAGE] = 1
    if SESSION_PAGE_SIZE not in st.session_state:
        st.session_state[SESSION_PAGE_SIZE] = DEFAULT_PAGE_SIZE

    current_filters = dict(st.session_state[SESSION_FILTERS])

    with st.form("employees_filters_form"):
        st.subheader("Filters")
        c1, c2, c3 = st.columns(3)
        with c1:
            employee_number = st.text_input(
                "Employee number",
                value=current_filters.get("employee_number") or "",
            )
            department_id_raw = st.text_input(
                "Department ID",
                value=str(current_filters.get("department_id") or ""),
                help="Exact department_id filter supported by GET /employees.",
            )
        with c2:
            name = st.text_input(
                "Name",
                value=current_filters.get("name") or "",
                help="Partial match on first or last name (API ``name``).",
            )
            status_options = [STATUS_FILTER_ALL, STATUS_ACTIVE, STATUS_INACTIVE]
            status_current = current_filters.get("status") or STATUS_FILTER_ALL
            if status_current not in status_options:
                status_current = STATUS_FILTER_ALL
            status = st.selectbox(
                "Status",
                options=status_options,
                index=status_options.index(status_current),
            )
        with c3:
            work_email = st.text_input(
                "Email",
                value=current_filters.get("work_email") or "",
                help="Partial match on work_email.",
            )
            page_size = st.selectbox(
                "Page size",
                options=[10, 20, 50, 100],
                index=[10, 20, 50, 100].index(
                    int(st.session_state[SESSION_PAGE_SIZE])
                )
                if int(st.session_state[SESSION_PAGE_SIZE]) in {10, 20, 50, 100}
                else 1,
            )
        apply_clicked = st.form_submit_button("Apply filters", type="primary")

    drafted_filters = {
        "employee_number": normalize_optional_text(employee_number),
        "name": normalize_optional_text(name),
        "work_email": normalize_optional_text(work_email),
        "department_id": normalize_department_id(department_id_raw),
        "status": normalize_status_filter(status) or STATUS_FILTER_ALL,
    }

    # Apply filters always restarts at page 1 (matches "reset pagination when filters change").
    if apply_clicked:
        st.session_state[SESSION_FILTERS] = drafted_filters
        st.session_state[SESSION_PAGE_SIZE] = int(page_size)
        st.session_state[SESSION_PAGE] = 1

    pager = st.columns([1, 1, 2])
    with pager[0]:
        page = st.number_input(
            "Page",
            min_value=1,
            value=int(st.session_state[SESSION_PAGE]),
            step=1,
            key="employees_page_input",
        )
    with pager[1]:
        refresh_clicked = st.button("Refresh", key="employees_refresh")

    if int(page) != int(st.session_state[SESSION_PAGE]):
        st.session_state[SESSION_PAGE] = int(page)

    active_filters = dict(st.session_state[SESSION_FILTERS])
    query = build_list_query(
        page=int(st.session_state[SESSION_PAGE]),
        page_size=int(st.session_state[SESSION_PAGE_SIZE]),
        filters=active_filters,
    )
    fingerprint = query_fingerprint(query)
    needs_fetch = (
        apply_clicked
        or refresh_clicked
        or st.session_state.get(SESSION_LAST_QUERY) != fingerprint
        or SESSION_SNAPSHOT not in st.session_state
    )

    client = api_client if api_client is not None else SmartHrisApiClient()
    if needs_fetch:
        _payload, error = fetch_and_store_employees(client, query)
        if error:
            st.warning(f"Could not load employees: {error}")

    snapshot = st.session_state.get(SESSION_SNAPSHOT)
    error = st.session_state.get(SESSION_ERROR)
    if not snapshot:
        if error:
            st.error(error)
        else:
            st.info("No employee data loaded yet.")
        return

    total = int(snapshot.get("total") or 0)
    pages = int(snapshot.get("pages") or 0)
    st.caption(
        f"Showing page {snapshot.get('page', query['page'])} of {pages or 1} "
        f"— {total} employee(s) total "
        f"(page size {snapshot.get('page_size', query['page_size'])})."
    )

    rows = employees_to_table_rows(list(snapshot.get("items") or []))
    if not rows:
        st.info("No employees match the current filters.")
        return
    st.dataframe(rows, width="stretch", hide_index=True)


# Streamlit page runner executes this file as __main__.
if __name__ == "__main__":
    render()
