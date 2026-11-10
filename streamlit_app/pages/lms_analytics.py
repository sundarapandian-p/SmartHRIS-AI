"""LMS Analytics page — read-only dashboards via GET /analytics/lms/*."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import NAV_LMS_ANALYTICS

STATUS_FILTER_ALL = "all"
COURSE_STATUS_ACTIVE = "active"
COURSE_STATUS_INACTIVE = "inactive"
ENROLMENT_STATUSES = ("enrolled", "in_progress", "completed", "cancelled")
EMPLOYEE_STATUSES = ("active", "inactive")
GRANULARITIES = ("day", "week", "month")

DEFAULT_PAGE_SIZE = 20
TOP_LIMIT = 10

SESSION_SUMMARY = "lms_summary_snapshot"
SESSION_SUMMARY_ERROR = "lms_summary_error"
SESSION_SUMMARY_FP = "lms_summary_fp"

SESSION_COURSES = "lms_courses_snapshot"
SESSION_COURSES_ERROR = "lms_courses_error"
SESSION_COURSES_FP = "lms_courses_fp"
SESSION_COURSE_FILTERS = "lms_course_filters"
SESSION_COURSE_PAGE = "lms_course_page"
SESSION_COURSE_PAGE_SIZE = "lms_course_page_size"

SESSION_TOP = "lms_top_snapshot"
SESSION_TOP_ERROR = "lms_top_error"
SESSION_TOP_FP = "lms_top_fp"

SESSION_EMPLOYEES = "lms_employees_snapshot"
SESSION_EMPLOYEES_ERROR = "lms_employees_error"
SESSION_EMPLOYEES_FP = "lms_employees_fp"
SESSION_EMPLOYEE_FILTERS = "lms_employee_filters"
SESSION_EMPLOYEE_PAGE = "lms_employee_page"
SESSION_EMPLOYEE_PAGE_SIZE = "lms_employee_page_size"

SESSION_TRENDS = "lms_trends_snapshot"
SESSION_TRENDS_ERROR = "lms_trends_error"
SESSION_TRENDS_FP = "lms_trends_fp"
SESSION_TREND_FILTERS = "lms_trend_filters"


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


def normalize_choice(value: str | None, *, allowed: tuple[str, ...]) -> str | None:
    if value is None or value == STATUS_FILTER_ALL:
        return None
    if value in allowed:
        return value
    return None


def completion_percentage_from_summary(summary: dict[str, Any]) -> float:
    """Match backend rule: completed / (enrolled + in_progress + completed)."""
    enrolled = int(summary.get("enrolled") or 0)
    in_progress = int(summary.get("in_progress") or 0)
    completed = int(summary.get("completed") or 0)
    denominator = enrolled + in_progress + completed
    if denominator <= 0:
        return 0.0
    return round((completed * 100.0) / denominator, 2)


def empty_course_filters() -> dict[str, Any]:
    return {"category": None, "course_status": STATUS_FILTER_ALL}


def empty_employee_filters() -> dict[str, Any]:
    return {"department_id": None, "employee_status": STATUS_FILTER_ALL}


def default_trend_filters() -> dict[str, Any]:
    today = date.today()
    return {
        "from_date": today - timedelta(days=30),
        "to_date": today,
        "granularity": "day",
        "status": STATUS_FILTER_ALL,
        "course_code": None,
    }


def build_course_query(
    *,
    page: int,
    page_size: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "page": int(page),
        "page_size": int(page_size),
        "category": normalize_optional_text(filters.get("category")),
        "course_status": normalize_choice(
            filters.get("course_status"),
            allowed=(COURSE_STATUS_ACTIVE, COURSE_STATUS_INACTIVE),
        ),
        "sort_by": "enrolments",
        "sort_order": "desc",
    }


def build_employee_query(
    *,
    page: int,
    page_size: int,
    filters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "page": int(page),
        "page_size": int(page_size),
        "department_id": normalize_department_id(filters.get("department_id")),
        "employee_status": normalize_choice(
            filters.get("employee_status"),
            allowed=EMPLOYEE_STATUSES,
        ),
        "sort_by": "enrolments",
        "sort_order": "desc",
    }


def build_trend_query(filters: dict[str, Any]) -> dict[str, Any]:
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    return {
        "from_date": from_date.isoformat() if isinstance(from_date, date) else str(from_date),
        "to_date": to_date.isoformat() if isinstance(to_date, date) else str(to_date),
        "granularity": normalize_choice(filters.get("granularity"), allowed=GRANULARITIES)
        or "day",
        "status": normalize_choice(filters.get("status"), allowed=ENROLMENT_STATUSES),
        "course_code": normalize_optional_text(filters.get("course_code")),
    }


def course_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "course_code": item.get("course_code"),
                "course_name": item.get("course_name"),
                "category": item.get("category"),
                "total_enrolments": item.get("total_enrolments"),
                "enrolled": item.get("enrolled"),
                "in_progress": item.get("in_progress"),
                "completed": item.get("completed"),
                "cancelled": item.get("cancelled"),
                "completion_percentage": item.get("completion_percentage"),
            }
        )
    return rows


def top_course_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "course_code": item.get("course_code"),
            "course_name": item.get("course_name"),
            "enrolment_count": item.get("enrolment_count"),
        }
        for item in items
    ]


def employee_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(
            {
                "employee_number": item.get("employee_number"),
                "first_name": item.get("first_name"),
                "last_name": item.get("last_name"),
                "department_id": item.get("department_id"),
                "total_enrolments": item.get("total_enrolments"),
                "enrolled": item.get("enrolled"),
                "in_progress": item.get("in_progress"),
                "completed": item.get("completed"),
                "cancelled": item.get("cancelled"),
                "completion_percentage": item.get("completion_percentage"),
            }
        )
    return rows


def trend_chart_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "period": item.get("period_label") or item.get("period_start"),
            "enrolments": int(item.get("enrolment_count") or 0),
        }
        for item in items
    ]


def _store_ok(session_key: str, error_key: str, fp_key: str, payload: dict[str, Any], fp: Any) -> None:
    st.session_state[session_key] = payload
    st.session_state[error_key] = None
    st.session_state[fp_key] = fp


def _store_error(error_key: str, message: str) -> None:
    st.session_state[error_key] = message


def fetch_summary(client: SmartHrisApiClient, *, force: bool = False) -> None:
    fp = ("summary",)
    if not force and st.session_state.get(SESSION_SUMMARY_FP) == fp and SESSION_SUMMARY in st.session_state:
        return
    try:
        payload = client.get_lms_summary().model_dump(mode="json")
        _store_ok(SESSION_SUMMARY, SESSION_SUMMARY_ERROR, SESSION_SUMMARY_FP, payload, fp)
    except ApiError as exc:
        _store_error(SESSION_SUMMARY_ERROR, str(exc))


def fetch_courses(client: SmartHrisApiClient, query: dict[str, Any], *, force: bool = False) -> None:
    fp = tuple(sorted(query.items()))
    if not force and st.session_state.get(SESSION_COURSES_FP) == fp and SESSION_COURSES in st.session_state:
        return
    try:
        payload = client.list_lms_courses(**query).model_dump(mode="json")
        _store_ok(SESSION_COURSES, SESSION_COURSES_ERROR, SESSION_COURSES_FP, payload, fp)
    except ApiError as exc:
        _store_error(SESSION_COURSES_ERROR, str(exc))


def fetch_top_courses(client: SmartHrisApiClient, *, force: bool = False) -> None:
    query = {"limit": TOP_LIMIT}
    fp = ("top", TOP_LIMIT)
    if not force and st.session_state.get(SESSION_TOP_FP) == fp and SESSION_TOP in st.session_state:
        return
    try:
        payload = client.list_lms_top_courses(**query).model_dump(mode="json")
        _store_ok(SESSION_TOP, SESSION_TOP_ERROR, SESSION_TOP_FP, payload, fp)
    except ApiError as exc:
        _store_error(SESSION_TOP_ERROR, str(exc))


def fetch_employees(client: SmartHrisApiClient, query: dict[str, Any], *, force: bool = False) -> None:
    fp = tuple(sorted(query.items()))
    if (
        not force
        and st.session_state.get(SESSION_EMPLOYEES_FP) == fp
        and SESSION_EMPLOYEES in st.session_state
    ):
        return
    try:
        payload = client.list_lms_employees(**query).model_dump(mode="json")
        _store_ok(SESSION_EMPLOYEES, SESSION_EMPLOYEES_ERROR, SESSION_EMPLOYEES_FP, payload, fp)
    except ApiError as exc:
        _store_error(SESSION_EMPLOYEES_ERROR, str(exc))


def fetch_trends(client: SmartHrisApiClient, query: dict[str, Any], *, force: bool = False) -> None:
    fp = tuple(sorted(query.items()))
    if not force and st.session_state.get(SESSION_TRENDS_FP) == fp and SESSION_TRENDS in st.session_state:
        return
    try:
        payload = client.list_lms_enrolment_trends(**query).model_dump(mode="json")
        _store_ok(SESSION_TRENDS, SESSION_TRENDS_ERROR, SESSION_TRENDS_FP, payload, fp)
    except ApiError as exc:
        _store_error(SESSION_TRENDS_ERROR, str(exc))


def _ensure_defaults() -> None:
    if SESSION_COURSE_FILTERS not in st.session_state:
        st.session_state[SESSION_COURSE_FILTERS] = empty_course_filters()
    if SESSION_COURSE_PAGE not in st.session_state:
        st.session_state[SESSION_COURSE_PAGE] = 1
    if SESSION_COURSE_PAGE_SIZE not in st.session_state:
        st.session_state[SESSION_COURSE_PAGE_SIZE] = DEFAULT_PAGE_SIZE
    if SESSION_EMPLOYEE_FILTERS not in st.session_state:
        st.session_state[SESSION_EMPLOYEE_FILTERS] = empty_employee_filters()
    if SESSION_EMPLOYEE_PAGE not in st.session_state:
        st.session_state[SESSION_EMPLOYEE_PAGE] = 1
    if SESSION_EMPLOYEE_PAGE_SIZE not in st.session_state:
        st.session_state[SESSION_EMPLOYEE_PAGE_SIZE] = DEFAULT_PAGE_SIZE
    if SESSION_TREND_FILTERS not in st.session_state:
        st.session_state[SESSION_TREND_FILTERS] = default_trend_filters()


def render_kpi_section(summary: dict[str, Any] | None, error: str | None) -> None:
    st.subheader("Overview")
    if error and not summary:
        st.error(error)
        return
    if error and summary:
        st.warning(f"Showing last loaded summary; refresh failed: {error}")
    if not summary:
        st.info("No LMS summary data loaded yet.")
        return

    pct = completion_percentage_from_summary(summary)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Employees", int(summary.get("total_employees") or 0))
    c2.metric("Courses", int(summary.get("total_courses") or 0))
    c3.metric("Enrolments", int(summary.get("total_enrolments") or 0))
    c4.metric("Completed", int(summary.get("completed") or 0))
    c5.metric("Completion %", f"{pct:.2f}")


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    """LMS analytics dashboards (read-only)."""
    st.title(NAV_LMS_ANALYTICS)
    st.caption(
        "SQL-backed LMS analytics from the FastAPI backend. "
        "Completion % excludes cancelled enrolments from the denominator."
    )
    _ensure_defaults()
    client = api_client if api_client is not None else SmartHrisApiClient()

    refresh_all = st.button("Refresh all", type="primary", key="lms_refresh_all")

    # --- Summary ---
    fetch_summary(client, force=refresh_all)
    render_kpi_section(
        st.session_state.get(SESSION_SUMMARY),
        st.session_state.get(SESSION_SUMMARY_ERROR),
    )

    st.divider()

    # --- Course analytics ---
    st.subheader("Course analytics")
    course_filters = dict(st.session_state[SESSION_COURSE_FILTERS])
    with st.form("lms_course_filters_form"):
        c1, c2, c3 = st.columns(3)
        with c1:
            category = st.text_input(
                "Category",
                value=course_filters.get("category") or "",
                help="Exact category match (API ``category``).",
            )
        with c2:
            status_options = [STATUS_FILTER_ALL, COURSE_STATUS_ACTIVE, COURSE_STATUS_INACTIVE]
            status_current = course_filters.get("course_status") or STATUS_FILTER_ALL
            if status_current not in status_options:
                status_current = STATUS_FILTER_ALL
            course_status = st.selectbox(
                "Course status",
                options=status_options,
                index=status_options.index(status_current),
            )
        with c3:
            course_page_size = st.selectbox(
                "Page size",
                options=[10, 20, 50, 100],
                index=[10, 20, 50, 100].index(int(st.session_state[SESSION_COURSE_PAGE_SIZE]))
                if int(st.session_state[SESSION_COURSE_PAGE_SIZE]) in {10, 20, 50, 100}
                else 1,
                key="lms_course_page_size_input",
            )
        apply_courses = st.form_submit_button("Apply course filters")

    if apply_courses:
        st.session_state[SESSION_COURSE_FILTERS] = {
            "category": normalize_optional_text(category),
            "course_status": course_status,
        }
        st.session_state[SESSION_COURSE_PAGE_SIZE] = int(course_page_size)
        st.session_state[SESSION_COURSE_PAGE] = 1

    course_pager = st.columns([1, 1, 2])
    with course_pager[0]:
        course_page = st.number_input(
            "Course page",
            min_value=1,
            value=int(st.session_state[SESSION_COURSE_PAGE]),
            step=1,
            key="lms_course_page_input",
        )
    if int(course_page) != int(st.session_state[SESSION_COURSE_PAGE]):
        st.session_state[SESSION_COURSE_PAGE] = int(course_page)

    course_query = build_course_query(
        page=int(st.session_state[SESSION_COURSE_PAGE]),
        page_size=int(st.session_state[SESSION_COURSE_PAGE_SIZE]),
        filters=dict(st.session_state[SESSION_COURSE_FILTERS]),
    )
    fetch_courses(client, course_query, force=refresh_all or apply_courses)

    courses_error = st.session_state.get(SESSION_COURSES_ERROR)
    courses_snapshot = st.session_state.get(SESSION_COURSES)
    if courses_error and not courses_snapshot:
        st.error(courses_error)
    elif courses_error:
        st.warning(f"Course refresh issue: {courses_error}")
    if courses_snapshot:
        total = int(courses_snapshot.get("total") or 0)
        pages = int(courses_snapshot.get("pages") or 0)
        st.caption(
            f"Courses page {courses_snapshot.get('page')} of {pages or 1} — {total} course(s)."
        )
        rows = course_rows(list(courses_snapshot.get("items") or []))
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("No courses match the current filters.")
    elif not courses_error:
        st.info("No course analytics loaded yet.")

    st.divider()

    # --- Top courses ---
    st.subheader(f"Top {TOP_LIMIT} courses by enrolments")
    fetch_top_courses(client, force=refresh_all)
    top_error = st.session_state.get(SESSION_TOP_ERROR)
    top_snapshot = st.session_state.get(SESSION_TOP)
    if top_error and not top_snapshot:
        st.error(top_error)
    elif top_snapshot:
        top_rows = top_course_rows(list(top_snapshot.get("items") or []))
        if top_rows:
            st.dataframe(top_rows, width="stretch", hide_index=True)
        else:
            st.info("No enrolment data available for top courses.")
    elif not top_error:
        st.info("No top-course data loaded yet.")

    st.divider()

    # --- Employee progress ---
    st.subheader("Employee learning progress")
    employee_filters = dict(st.session_state[SESSION_EMPLOYEE_FILTERS])
    with st.form("lms_employee_filters_form"):
        e1, e2, e3 = st.columns(3)
        with e1:
            department_id_raw = st.text_input(
                "Department ID",
                value=str(employee_filters.get("department_id") or ""),
            )
        with e2:
            emp_status_options = [STATUS_FILTER_ALL, *EMPLOYEE_STATUSES]
            emp_status_current = employee_filters.get("employee_status") or STATUS_FILTER_ALL
            if emp_status_current not in emp_status_options:
                emp_status_current = STATUS_FILTER_ALL
            employee_status = st.selectbox(
                "Employee status",
                options=emp_status_options,
                index=emp_status_options.index(emp_status_current),
            )
        with e3:
            employee_page_size = st.selectbox(
                "Page size",
                options=[10, 20, 50, 100],
                index=[10, 20, 50, 100].index(int(st.session_state[SESSION_EMPLOYEE_PAGE_SIZE]))
                if int(st.session_state[SESSION_EMPLOYEE_PAGE_SIZE]) in {10, 20, 50, 100}
                else 1,
                key="lms_employee_page_size_input",
            )
        apply_employees = st.form_submit_button("Apply employee filters")

    if apply_employees:
        st.session_state[SESSION_EMPLOYEE_FILTERS] = {
            "department_id": normalize_department_id(department_id_raw),
            "employee_status": employee_status,
        }
        st.session_state[SESSION_EMPLOYEE_PAGE_SIZE] = int(employee_page_size)
        st.session_state[SESSION_EMPLOYEE_PAGE] = 1

    emp_pager = st.columns([1, 1, 2])
    with emp_pager[0]:
        employee_page = st.number_input(
            "Employee page",
            min_value=1,
            value=int(st.session_state[SESSION_EMPLOYEE_PAGE]),
            step=1,
            key="lms_employee_page_input",
        )
    if int(employee_page) != int(st.session_state[SESSION_EMPLOYEE_PAGE]):
        st.session_state[SESSION_EMPLOYEE_PAGE] = int(employee_page)

    employee_query = build_employee_query(
        page=int(st.session_state[SESSION_EMPLOYEE_PAGE]),
        page_size=int(st.session_state[SESSION_EMPLOYEE_PAGE_SIZE]),
        filters=dict(st.session_state[SESSION_EMPLOYEE_FILTERS]),
    )
    fetch_employees(client, employee_query, force=refresh_all or apply_employees)

    emp_error = st.session_state.get(SESSION_EMPLOYEES_ERROR)
    emp_snapshot = st.session_state.get(SESSION_EMPLOYEES)
    if emp_error and not emp_snapshot:
        st.error(emp_error)
    elif emp_error:
        st.warning(f"Employee progress refresh issue: {emp_error}")
    if emp_snapshot:
        total = int(emp_snapshot.get("total") or 0)
        pages = int(emp_snapshot.get("pages") or 0)
        st.caption(
            f"Employees page {emp_snapshot.get('page')} of {pages or 1} — {total} employee(s)."
        )
        rows = employee_rows(list(emp_snapshot.get("items") or []))
        if rows:
            st.dataframe(rows, width="stretch", hide_index=True)
        else:
            st.info("No employees match the current filters.")
    elif not emp_error:
        st.info("No employee progress data loaded yet.")

    st.divider()

    # --- Trends ---
    st.subheader("Enrolment trends")
    trend_filters = dict(st.session_state[SESSION_TREND_FILTERS])
    with st.form("lms_trend_filters_form"):
        t1, t2, t3 = st.columns(3)
        with t1:
            from_date = st.date_input(
                "From date",
                value=trend_filters.get("from_date") or date.today() - timedelta(days=30),
            )
            to_date = st.date_input(
                "To date",
                value=trend_filters.get("to_date") or date.today(),
            )
        with t2:
            granularity_current = trend_filters.get("granularity") or "day"
            if granularity_current not in GRANULARITIES:
                granularity_current = "day"
            granularity = st.selectbox(
                "Granularity",
                options=list(GRANULARITIES),
                index=list(GRANULARITIES).index(granularity_current),
            )
            status_options = [STATUS_FILTER_ALL, *ENROLMENT_STATUSES]
            status_current = trend_filters.get("status") or STATUS_FILTER_ALL
            if status_current not in status_options:
                status_current = STATUS_FILTER_ALL
            trend_status = st.selectbox(
                "Enrolment status",
                options=status_options,
                index=status_options.index(status_current),
            )
        with t3:
            course_code = st.text_input(
                "Course code",
                value=trend_filters.get("course_code") or "",
                help="Optional exact course_code filter.",
            )
        apply_trends = st.form_submit_button("Apply trend filters")

    if apply_trends:
        st.session_state[SESSION_TREND_FILTERS] = {
            "from_date": from_date,
            "to_date": to_date,
            "granularity": granularity,
            "status": trend_status,
            "course_code": normalize_optional_text(course_code),
        }

    trend_query = build_trend_query(dict(st.session_state[SESSION_TREND_FILTERS]))
    fetch_trends(client, trend_query, force=refresh_all or apply_trends)

    trends_error = st.session_state.get(SESSION_TRENDS_ERROR)
    trends_snapshot = st.session_state.get(SESSION_TRENDS)
    if trends_error and not trends_snapshot:
        st.error(trends_error)
    elif trends_error:
        st.warning(f"Trend refresh issue: {trends_error}")
    if trends_snapshot:
        chart_rows = trend_chart_rows(list(trends_snapshot.get("items") or []))
        if chart_rows:
            st.line_chart(chart_rows, x="period", y="enrolments")
            st.dataframe(chart_rows, width="stretch", hide_index=True)
        else:
            st.info("No enrolments in the selected date range.")
    elif not trends_error:
        st.info("No trend data loaded yet.")


if __name__ == "__main__":
    render()
