"""Phase 5.3.4 — LMS Analytics Streamlit UI and API client tests (mocked HTTP)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from smarthris.schemas.lms_analytics import (
    CourseAnalyticsListResponse,
    EmployeeAnalyticsListResponse,
    EnrolmentTrendsResponse,
    LmsSummaryResponse,
    TopCoursesResponse,
)
from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import NAV_LMS_ANALYTICS
from streamlit_app.pages.lms_analytics import (
    SESSION_COURSES,
    SESSION_COURSES_ERROR,
    SESSION_EMPLOYEES,
    SESSION_SUMMARY,
    SESSION_SUMMARY_ERROR,
    SESSION_TOP,
    SESSION_TRENDS,
    SESSION_TRENDS_ERROR,
    build_course_query,
    build_employee_query,
    build_trend_query,
    completion_percentage_from_summary,
    course_rows,
    employee_rows,
    fetch_summary,
    top_course_rows,
    trend_chart_rows,
)

_TIMEOUT = 10


def _summary(**overrides: object) -> dict[str, Any]:
    base = {
        "total_employees": 10,
        "total_courses": 5,
        "total_enrolments": 20,
        "enrolled": 4,
        "in_progress": 6,
        "completed": 8,
        "cancelled": 2,
    }
    base.update(overrides)
    return base


def _course_item(**overrides: object) -> dict[str, Any]:
    base = {
        "course_code": "PYTHON101",
        "course_name": "Python 101",
        "category": "Engineering",
        "total_enrolments": 10,
        "enrolled": 2,
        "in_progress": 3,
        "completed": 4,
        "cancelled": 1,
        "completion_percentage": "44.44",
    }
    base.update(overrides)
    return base


def _employee_item(**overrides: object) -> dict[str, Any]:
    base = {
        "employee_number": "EMP0001",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "department_id": 1,
        "total_enrolments": 3,
        "enrolled": 1,
        "in_progress": 0,
        "completed": 2,
        "cancelled": 0,
        "completion_percentage": "66.67",
    }
    base.update(overrides)
    return base


def _client(handler) -> SmartHrisApiClient:
    return SmartHrisApiClient(
        base_url="http://test.local",
        transport=httpx.MockTransport(handler),
    )


class RecordingAnalyticsClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.summary = LmsSummaryResponse.model_validate(_summary())
        self.courses = CourseAnalyticsListResponse.model_validate(
            {
                "items": [_course_item()],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "pages": 1,
            }
        )
        self.top = TopCoursesResponse.model_validate(
            {
                "items": [
                    {
                        "course_code": "PYTHON101",
                        "course_name": "Python 101",
                        "enrolment_count": 10,
                    }
                ],
                "limit": 10,
            }
        )
        self.employees = EmployeeAnalyticsListResponse.model_validate(
            {
                "items": [_employee_item()],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "pages": 1,
            }
        )
        self.trends = EnrolmentTrendsResponse.model_validate(
            {
                "granularity": "day",
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
                "items": [
                    {
                        "period_start": "2026-01-05",
                        "period_label": "2026-01-05",
                        "enrolment_count": 3,
                    }
                ],
            }
        )
        self.fail_summary: Exception | None = None
        self.fail_trends: Exception | None = None

    def get_lms_summary(self) -> LmsSummaryResponse:
        self.calls.append("summary")
        if self.fail_summary:
            raise self.fail_summary
        return self.summary

    def list_lms_courses(self, **kwargs: Any) -> CourseAnalyticsListResponse:
        self.calls.append(f"courses:{kwargs}")
        return self.courses

    def list_lms_top_courses(self, **kwargs: Any) -> TopCoursesResponse:
        self.calls.append(f"top:{kwargs}")
        return self.top

    def list_lms_employees(self, **kwargs: Any) -> EmployeeAnalyticsListResponse:
        self.calls.append(f"employees:{kwargs}")
        return self.employees

    def list_lms_enrolment_trends(self, **kwargs: Any) -> EnrolmentTrendsResponse:
        self.calls.append(f"trends:{kwargs}")
        if self.fail_trends:
            raise self.fail_trends
        return self.trends


def _run_page(api_client=None) -> AppTest:
    def _script(client=None) -> None:
        from streamlit_app.pages.lms_analytics import render as render_lms

        render_lms(api_client=client)

    return AppTest.from_function(
        _script,
        default_timeout=_TIMEOUT,
        kwargs={"client": api_client},
    ).run()


def test_completion_percentage_matches_backend_rule() -> None:
    assert completion_percentage_from_summary(_summary()) == 44.44
    assert completion_percentage_from_summary(_summary(enrolled=0, in_progress=0, completed=0)) == 0.0
    # Cancelled ignored in denominator: completed=2, cancelled=5 → 100%
    assert (
        completion_percentage_from_summary(
            _summary(enrolled=0, in_progress=0, completed=2, cancelled=5)
        )
        == 100.0
    )


def test_query_builders() -> None:
    course_q = build_course_query(
        page=2,
        page_size=10,
        filters={"category": " Engineering ", "course_status": "active"},
    )
    assert course_q["category"] == "Engineering"
    assert course_q["course_status"] == "active"
    assert course_q["page"] == 2

    emp_q = build_employee_query(
        page=1,
        page_size=20,
        filters={"department_id": "3", "employee_status": "all"},
    )
    assert emp_q["department_id"] == 3
    assert emp_q["employee_status"] is None

    trend_q = build_trend_query(
        {
            "from_date": date(2026, 1, 1),
            "to_date": date(2026, 1, 31),
            "granularity": "week",
            "status": "completed",
            "course_code": " PYTHON101 ",
        }
    )
    assert trend_q["from_date"] == "2026-01-01"
    assert trend_q["granularity"] == "week"
    assert trend_q["course_code"] == "PYTHON101"


def test_table_row_helpers() -> None:
    assert course_rows([_course_item()])[0]["course_code"] == "PYTHON101"
    assert top_course_rows(
        [{"course_code": "A", "course_name": "B", "enrolment_count": 1}]
    )[0]["enrolment_count"] == 1
    assert employee_rows([_employee_item()])[0]["employee_number"] == "EMP0001"
    assert trend_chart_rows(
        [{"period_label": "2026-01-05", "period_start": "2026-01-05", "enrolment_count": 2}]
    )[0] == {"period": "2026-01-05", "enrolments": 2}


def test_api_client_lms_methods_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/analytics/lms/summary":
            return httpx.Response(200, json=_summary())
        if path == "/analytics/lms/courses":
            assert request.url.params.get("page") == "1"
            assert request.url.params.get("category") == "Engineering"
            return httpx.Response(
                200,
                json={
                    "items": [_course_item()],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                    "pages": 1,
                },
            )
        if path == "/analytics/lms/courses/top":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "course_code": "PYTHON101",
                            "course_name": "Python 101",
                            "enrolment_count": 10,
                        }
                    ],
                    "limit": 10,
                },
            )
        if path == "/analytics/lms/employees":
            return httpx.Response(
                200,
                json={
                    "items": [_employee_item()],
                    "total": 1,
                    "page": 1,
                    "page_size": 20,
                    "pages": 1,
                },
            )
        if path == "/analytics/lms/trends/enrolments":
            assert request.url.params.get("granularity") == "month"
            return httpx.Response(
                200,
                json={
                    "granularity": "month",
                    "from_date": "2026-01-01",
                    "to_date": "2026-03-31",
                    "items": [
                        {
                            "period_start": "2026-01-01",
                            "period_label": "2026-01",
                            "enrolment_count": 4,
                        }
                    ],
                },
            )
        return httpx.Response(404, json={"detail": "missing"})

    client = _client(handler)
    assert client.get_lms_summary().total_employees == 10
    courses = client.list_lms_courses(page=1, category="Engineering")
    assert courses.items[0].course_code == "PYTHON101"
    top = client.list_lms_top_courses(limit=10)
    assert top.items[0].enrolment_count == 10
    employees = client.list_lms_employees(page=1)
    assert employees.items[0].employee_number == "EMP0001"
    trends = client.list_lms_enrolment_trends(
        from_date="2026-01-01",
        to_date="2026-03-31",
        granularity="month",
    )
    assert trends.items[0].enrolment_count == 4


def test_api_client_lms_summary_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom", "code": "app_error"})

    with pytest.raises(ApiError, match="boom") as exc_info:
        _client(handler).get_lms_summary()
    assert exc_info.value.status_code == 500


def test_fetch_summary_stores_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _State(dict):
        pass

    state = _State()

    class _SessionProxy:
        def __getattr__(self, name: str):
            raise AttributeError(name)

        def __contains__(self, key: object) -> bool:
            return key in state

        def get(self, key: object, default: object = None) -> object:
            return state.get(key, default)

        def __getitem__(self, key: object) -> object:
            return state[key]

        def __setitem__(self, key: object, value: object) -> None:
            state[key] = value

    import streamlit_app.pages.lms_analytics as page

    monkeypatch.setattr(page.st, "session_state", _SessionProxy())
    client = RecordingAnalyticsClient()
    client.fail_summary = ApiError("offline", status_code=503)
    fetch_summary(client, force=True)  # type: ignore[arg-type]
    assert state[SESSION_SUMMARY_ERROR] == "offline"


def test_page_renders_kpis_and_tables() -> None:
    client = RecordingAnalyticsClient()
    at = _run_page(client)
    assert not at.exception
    titles = [t.value for t in at.title]
    assert NAV_LMS_ANALYTICS in titles
    assert "summary" in client.calls
    assert any(c.startswith("courses:") for c in client.calls)
    assert any(c.startswith("top:") for c in client.calls)
    assert any(c.startswith("employees:") for c in client.calls)
    assert any(c.startswith("trends:") for c in client.calls)
    assert SESSION_SUMMARY in at.session_state
    assert SESSION_COURSES in at.session_state
    assert SESSION_TOP in at.session_state
    assert SESSION_EMPLOYEES in at.session_state
    assert SESSION_TRENDS in at.session_state
    metrics = [m.label for m in at.metric]
    assert "Employees" in metrics
    assert "Completion %" in metrics


def test_page_handles_empty_payloads() -> None:
    client = RecordingAnalyticsClient()
    client.summary = LmsSummaryResponse.model_validate(_summary(total_enrolments=0, completed=0, enrolled=0, in_progress=0, cancelled=0))
    client.courses = CourseAnalyticsListResponse.model_validate(
        {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}
    )
    client.top = TopCoursesResponse.model_validate({"items": [], "limit": 10})
    client.employees = EmployeeAnalyticsListResponse.model_validate(
        {"items": [], "total": 0, "page": 1, "page_size": 20, "pages": 0}
    )
    client.trends = EnrolmentTrendsResponse.model_validate(
        {
            "granularity": "day",
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "items": [],
        }
    )
    at = _run_page(client)
    assert not at.exception
    infos = " ".join(i.value for i in at.info)
    assert "No courses match" in infos or "No enrolment data" in infos or "No enrolments" in infos


def test_page_handles_api_failure() -> None:
    client = RecordingAnalyticsClient()
    client.fail_summary = ApiError("API down", status_code=503)
    client.fail_trends = ApiError("trends failed", status_code=400)
    at = _run_page(client)
    assert not at.exception
    assert at.session_state.get(SESSION_SUMMARY_ERROR) == "API down"
    assert at.session_state.get(SESSION_TRENDS_ERROR) == "trends failed"
    errors = " ".join(e.value for e in at.error)
    assert "API down" in errors or "trends failed" in errors


def test_page_avoids_duplicate_fetches_on_rerun() -> None:
    client = RecordingAnalyticsClient()
    at = _run_page(client)
    first_count = len(client.calls)
    at.run()
    # Session fingerprints should prevent a full refetch storm; call count should not double.
    assert len(client.calls) < first_count * 2
