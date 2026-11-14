"""Phase 4.6 — Employee listing UI tests (no live API or MySQL)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from smarthris.schemas.employee import EmployeeListResponse
from streamlit_app.api_client import ApiError
from streamlit_app.pages.employees import (
    SESSION_ERROR,
    SESSION_FILTERS,
    SESSION_PAGE,
    SESSION_SNAPSHOT,
    build_list_query,
    employees_to_table_rows,
    empty_filters,
    fetch_and_store_employees,
    filters_fingerprint,
    normalize_department_id,
    normalize_optional_text,
    normalize_status_filter,
)

_NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
_TIMEOUT = 10


def _employee(**overrides: object) -> dict[str, Any]:
    base = {
        "id": 1,
        "employee_number": "E001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "work_email": "ada@example.com",
        "department_id": 2,
        "job_role": None,
        "job_title": "Engineer",
        "manager_employee_id": None,
        "hire_date": None,
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


def _list_payload(
    items: list[dict[str, Any]] | None = None,
    *,
    page: int = 1,
    page_size: int = 20,
    total: int | None = None,
) -> EmployeeListResponse:
    rows = items if items is not None else [_employee()]
    total_count = len(rows) if total is None else total
    pages = (total_count + page_size - 1) // page_size if total_count else 0
    return EmployeeListResponse.model_validate(
        {
            "items": rows,
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
    )


class RecordingClient:
    def __init__(self, payload: EmployeeListResponse | None = None) -> None:
        self.payload = payload or _list_payload()
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    def list_employees(self, **kwargs: Any) -> EmployeeListResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.payload


def _run_page(api_client=None) -> AppTest:
    def _script(client=None) -> None:
        from streamlit_app.auth import enable_page_guard_bypass_for_tests
        from streamlit_app.pages.employees import render as render_employees

        enable_page_guard_bypass_for_tests()
        render_employees(api_client=client)

    return AppTest.from_function(
        _script,
        default_timeout=_TIMEOUT,
        kwargs={"client": api_client},
    ).run()


def test_normalize_helpers() -> None:
    assert normalize_optional_text("  Ada ") == "Ada"
    assert normalize_optional_text("   ") is None
    assert normalize_department_id("3") == 3
    assert normalize_department_id("0") is None
    assert normalize_department_id("") is None
    assert normalize_status_filter("all") is None
    assert normalize_status_filter("active") == "active"
    assert normalize_status_filter("inactive") == "inactive"


def test_build_list_query_uses_backend_param_names() -> None:
    query = build_list_query(
        page=2,
        page_size=10,
        filters={
            "employee_number": "E0",
            "name": "Ada",
            "work_email": "ada@",
            "department_id": 4,
            "status": "active",
        },
    )
    assert query == {
        "page": 2,
        "page_size": 10,
        "employee_number": "E0",
        "name": "Ada",
        "work_email": "ada@",
        "department_id": 4,
        "status": "active",
    }


def test_filters_fingerprint_changes_when_filters_change() -> None:
    a = empty_filters()
    b = empty_filters() | {"name": "Ada"}
    assert filters_fingerprint(a) != filters_fingerprint(b)


def test_employees_to_table_rows_available_fields() -> None:
    rows = employees_to_table_rows([_employee()])
    assert rows == [
        {
            "employee_number": "E001",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "work_email": "ada@example.com",
            "department_id": 2,
            "job_title": "Engineer",
            "status": "active",
        }
    ]


def test_fetch_and_store_employees_success(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {}
    monkeypatch.setattr("streamlit_app.pages.employees.st.session_state", session)
    client = RecordingClient(_list_payload([_employee(id=9, employee_number="E9")]))
    query = build_list_query(page=1, page_size=20, filters=empty_filters())
    payload, err = fetch_and_store_employees(client, query)  # type: ignore[arg-type]
    assert err is None
    assert payload is not None
    assert payload["items"][0]["employee_number"] == "E9"
    assert session[SESSION_SNAPSHOT]["total"] == 1
    assert client.calls[0]["page"] == 1


def test_fetch_and_store_employees_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {
        SESSION_SNAPSHOT: {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}
    }
    monkeypatch.setattr("streamlit_app.pages.employees.st.session_state", session)
    client = RecordingClient()
    client.error = ApiError("Cannot reach the API at http://test.local.")
    query = build_list_query(page=1, page_size=20, filters=empty_filters())
    payload, err = fetch_and_store_employees(client, query)  # type: ignore[arg-type]
    assert err is not None
    assert "Cannot reach the API" in err
    assert session[SESSION_ERROR]
    assert payload == session[SESSION_SNAPSHOT]


def test_page_renders_and_lists_employees() -> None:
    client = RecordingClient(
        _list_payload(
            [
                _employee(),
                _employee(
                    id=2,
                    employee_number="E002",
                    first_name="Grace",
                    last_name="Hopper",
                    work_email="grace@example.com",
                    status="inactive",
                    job_title="Admiral",
                ),
            ],
            total=2,
        )
    )
    at = _run_page(api_client=client)
    assert not at.exception
    assert any("Employees" in t.value for t in at.title)
    assert client.calls
    assert at.dataframe
    assert any("2 employee" in c.value for c in at.caption)


def test_empty_results_ui() -> None:
    client = RecordingClient(_list_payload([], total=0))
    at = _run_page(api_client=client)
    assert any("No employees match" in i.value for i in at.info)


def test_apply_filters_sends_search_and_resets_page() -> None:
    client = RecordingClient(_list_payload([], total=0))
    at = _run_page(api_client=client)
    at.session_state[SESSION_PAGE] = 3
    assert len(at.text_input) >= 4
    at.text_input[0].set_value("E10")
    at.text_input[2].set_value("Ada")
    at.text_input[3].set_value("ada@")
    at.form_submit_button[0].click().run()
    assert client.calls
    last = client.calls[-1]
    assert last["employee_number"] == "E10"
    assert last["name"] == "Ada"
    assert last["work_email"] == "ada@"
    assert last["page"] == 1
    assert at.session_state[SESSION_PAGE] == 1


def test_department_and_status_filters() -> None:
    client = RecordingClient(_list_payload([], total=0))
    at = _run_page(api_client=client)
    at.text_input[1].set_value("7")  # Department ID
    # Status selectbox is the first selectbox in the form.
    status_box = at.selectbox[0]
    status_box.set_value("inactive")
    at.form_submit_button[0].click().run()
    last = client.calls[-1]
    assert last["department_id"] == 7
    assert last["status"] == "inactive"
    assert last["page"] == 1


def test_pagination_page_change() -> None:
    client = RecordingClient(
        _list_payload([_employee()], page=2, page_size=20, total=40)
    )
    at = _run_page(api_client=client)
    at.number_input[0].set_value(2).run()
    assert client.calls[-1]["page"] == 2


def test_refresh_reloads_current_query() -> None:
    client = RecordingClient(_list_payload([_employee()], total=1))
    at = _run_page(api_client=client)
    initial = len(client.calls)
    refresh = [b for b in at.button if (b.label or "") == "Refresh"]
    assert refresh
    refresh[0].click().run()
    assert len(client.calls) > initial


def test_api_error_ui() -> None:
    client = RecordingClient()
    client.error = ApiError("The API request timed out.")
    at = _run_page(api_client=client)
    assert any("timed out" in w.value.lower() for w in at.warning) or any(
        "timed out" in e.value.lower() for e in at.error
    )
    assert SESSION_FILTERS in at.session_state


def test_filter_change_resets_pagination_helper() -> None:
    """Applying new filters must force page 1 in query construction path."""
    filters = empty_filters() | {"name": "Zoe", "status": "all"}
    query = build_list_query(page=1, page_size=20, filters=filters)
    assert query["page"] == 1
    assert query["name"] == "Zoe"
    assert query["status"] is None
