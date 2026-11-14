"""Phase 4.3/4.4 — Bulk Import upload + job status polling tests (no live API/DB)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from smarthris.schemas.import_job import ImportErrorListResponse, ImportJobRead
from streamlit_app.api_client import ApiError
from streamlit_app.pages.bulk_import import (
    MAX_UPLOAD_BYTES,
    PREVIEW_ROW_LIMIT,
    SESSION_ERROR_FETCH_ERROR,
    SESSION_ERROR_SNAPSHOT,
    SESSION_IMPORT_TARGET,
    SESSION_IN_FLIGHT,
    SESSION_JOB_ID,
    SESSION_JOB_SNAPSHOT,
    SESSION_LAST_STATUS,
    SESSION_POLL_ENABLED,
    SESSION_RETRY_IN_FLIGHT,
    SESSION_SOURCE_JOB_ID,
    SESSION_STATUS_ERROR,
    TARGET_COURSES,
    TARGET_EMPLOYEES,
    TARGET_ENROLMENTS,
    CsvPreviewError,
    error_list_to_rows,
    fetch_and_store_import_errors,
    fetch_and_store_job_status,
    is_active_status,
    is_retry_eligible,
    is_terminal_status,
    job_snapshot_from_model,
    normalize_import_target,
    normalize_status,
    preview_csv,
    should_auto_poll,
    should_fetch_import_errors,
    target_guidance,
    upload_for_target,
    validate_csv_selection,
)

_NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
_TIMEOUT = 10


def _csv_bytes(*, rows: int = 3, bom: bool = False) -> bytes:
    lines = [
        "Employee Number,First Name,Last Name,Work Email,Department ID",
    ]
    for i in range(rows):
        lines.append(f"E{i},Ada,Lovelace,ada{i}@example.com,1")
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    if bom:
        return b"\xef\xbb\xbf" + payload
    return payload


def _job(**overrides: object) -> ImportJobRead:
    base = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": "pending",
        "filename": "employees.csv",
        "file_type": "csv",
        "dry_run": False,
        "total_rows": 0,
        "processed_rows": 0,
        "success_count": 0,
        "failed_count": 0,
        "error_message": None,
        "parent_job_id": None,
        "created_at": _NOW,
        "started_at": None,
        "finished_at": None,
    }
    base.update(overrides)
    return ImportJobRead.model_validate(base)


def _error_list(
    job_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    total: int = 0,
    items: list[dict[str, Any]] | None = None,
) -> ImportErrorListResponse:
    rows = items or []
    pages = (total + page_size - 1) // page_size if total else 0
    return ImportErrorListResponse.model_validate(
        {
            "import_job_id": job_id,
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
        }
    )


class RecordingClient:
    """Fake API client recording upload/status/error/retry calls."""

    def __init__(self, job: ImportJobRead | None = None) -> None:
        self.job = job or _job()
        self.upload_calls: list[dict[str, Any]] = []
        self.upload_endpoints: list[str] = []
        self.get_calls: list[str] = []
        self.error_calls: list[dict[str, Any]] = []
        self.retry_calls: list[str] = []
        self.get_error: Exception | None = None
        self.list_errors_error: Exception | None = None
        self.retry_error: Exception | None = None
        self.get_sequence: list[ImportJobRead] | None = None
        self.error_payload: ImportErrorListResponse | None = None
        self.retry_result: ImportJobRead | None = None
        self._get_index = 0

    def upload_import(self, **kwargs: Any) -> ImportJobRead:
        self.upload_endpoints.append("/imports")
        self.upload_calls.append(kwargs)
        return self.job

    def upload_course_import(self, **kwargs: Any) -> ImportJobRead:
        self.upload_endpoints.append("/imports/courses")
        self.upload_calls.append(kwargs)
        return self.job

    def upload_enrolment_import(self, **kwargs: Any) -> ImportJobRead:
        self.upload_endpoints.append("/imports/enrolments")
        self.upload_calls.append(kwargs)
        return self.job

    def get_import_job(self, job_id: str) -> ImportJobRead:
        self.get_calls.append(job_id)
        if self.get_error is not None:
            raise self.get_error
        if self.get_sequence is not None:
            idx = min(self._get_index, len(self.get_sequence) - 1)
            self._get_index += 1
            return self.get_sequence[idx]
        if self.job.id == job_id:
            return self.job
        return _job(id=job_id, status=normalize_status(self.job.status))

    def list_import_errors(
        self,
        job_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> ImportErrorListResponse:
        self.error_calls.append(
            {"job_id": job_id, "page": page, "page_size": page_size}
        )
        if self.list_errors_error is not None:
            raise self.list_errors_error
        if self.error_payload is not None:
            return self.error_payload
        return _error_list(job_id, page=page, page_size=page_size, total=0, items=[])

    def retry_import(self, job_id: str) -> ImportJobRead:
        self.retry_calls.append(job_id)
        if self.retry_error is not None:
            raise self.retry_error
        if self.retry_result is not None:
            return self.retry_result
        return _job(id="child-job-1", status="pending", parent_job_id=job_id)


def _run_page(api_client=None) -> AppTest:
    """Drive ``render`` via AppTest (function body must be import-complete)."""

    def _script(client=None) -> None:
        from streamlit_app.auth import enable_page_guard_bypass_for_tests
        from streamlit_app.pages.bulk_import import render as render_bulk_import

        enable_page_guard_bypass_for_tests()
        render_bulk_import(api_client=client)

    return AppTest.from_function(
        _script,
        default_timeout=_TIMEOUT,
        kwargs={"client": api_client},
    ).run()


def _upload_csv(at: AppTest, content: bytes | None = None) -> AppTest:
    payload = _csv_bytes(rows=2) if content is None else content
    at.file_uploader[0].set_value(("employees.csv", payload, "text/csv")).run()
    at.button[0].click().run()
    return at


# --- Pure helper unit tests (upload) ----------------------------------------


def test_validate_no_filename() -> None:
    assert validate_csv_selection(filename=None, size=1, content=b"x") is not None
    assert validate_csv_selection(filename="  ", size=1, content=b"x") is not None


def test_validate_invalid_extension() -> None:
    message = validate_csv_selection(
        filename="people.xlsx",
        size=10,
        content=b"PK\x03\x04fake",
    )
    assert message is not None
    assert "CSV" in message


def test_validate_empty_csv() -> None:
    message = validate_csv_selection(filename="empty.csv", size=0, content=b"")
    assert message is not None
    assert "empty" in message.lower()


def test_validate_oversized_file() -> None:
    content = b"a" * (MAX_UPLOAD_BYTES + 1)
    message = validate_csv_selection(
        filename="big.csv",
        size=len(content),
        content=content,
    )
    assert message is not None
    assert str(MAX_UPLOAD_BYTES) in message


def test_validate_accepts_valid_csv() -> None:
    content = _csv_bytes()
    assert (
        validate_csv_selection(
            filename="employees.CSV",
            size=len(content),
            content=content,
        )
        is None
    )


def test_preview_valid_limits_to_ten_rows() -> None:
    content = _csv_bytes(rows=25)
    preview = preview_csv(content, max_rows=PREVIEW_ROW_LIMIT)
    assert preview.headers[0] == "Employee Number"
    assert len(preview.rows) == PREVIEW_ROW_LIMIT
    assert preview.truncated is True


def test_preview_utf8_bom() -> None:
    content = _csv_bytes(rows=1, bom=True)
    preview = preview_csv(content)
    assert preview.headers[0] == "Employee Number"
    assert preview.rows[0][0] == "E0"


def test_preview_malformed_csv_raises() -> None:
    bad = b'Employee Number,First Name\n"Ada,Lovelace\n'
    with pytest.raises(CsvPreviewError, match="Malformed CSV"):
        preview_csv(bad)


def test_preview_does_not_require_pandas_dataframe() -> None:
    preview = preview_csv(_csv_bytes(rows=2))
    assert isinstance(preview.rows, list)
    assert all(isinstance(row, list) for row in preview.rows)


# --- Status helper unit tests (polling) -------------------------------------


def test_status_helpers_pending_running_completed_failed() -> None:
    assert normalize_status("pending") == "pending"
    assert is_active_status("pending")
    assert is_active_status("running")
    assert not is_terminal_status("pending")
    assert not is_terminal_status("running")
    assert is_terminal_status("completed")
    assert is_terminal_status("failed")
    assert not is_active_status("completed")


def test_should_auto_poll_rules() -> None:
    assert should_auto_poll(job_id=None, status="pending", poll_enabled=True) is False
    assert should_auto_poll(job_id="j1", status="pending", poll_enabled=False) is False
    assert should_auto_poll(job_id="j1", status="pending", poll_enabled=True) is True
    assert should_auto_poll(job_id="j1", status="running", poll_enabled=True) is True
    assert should_auto_poll(job_id="j1", status="completed", poll_enabled=True) is False
    assert should_auto_poll(job_id="j1", status="failed", poll_enabled=True) is False
    assert should_auto_poll(job_id="j1", status=None, poll_enabled=True) is True


def test_no_polling_fetch_without_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def get_import_job(self, job_id: str) -> ImportJobRead:
            raise AssertionError("must not call API without Job ID")

    # Simulate empty session via Streamlit runtime is heavy; call helper directly.
    monkeypatch.setattr(
        "streamlit_app.pages.bulk_import.st.session_state",
        {},
        raising=False,
    )
    # Use a tiny stub session state mapping.
    state: dict[str, Any] = {}

    class _State(dict):
        def get(self, key, default=None):  # noqa: ANN001
            return dict.get(self, key, default)

    session = _State(state)
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    snapshot, error = fetch_and_store_job_status(Boom(), "")  # type: ignore[arg-type]
    assert snapshot is None
    assert error is not None
    assert SESSION_JOB_ID not in session or not session.get(SESSION_JOB_ID)


def test_fetch_pending_and_running_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {}
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    client = RecordingClient(_job(status="pending"))
    snap, err = fetch_and_store_job_status(client, client.job.id)  # type: ignore[arg-type]
    assert err is None
    assert snap is not None
    assert snap["status"] == "pending"
    assert session[SESSION_JOB_ID] == client.job.id
    assert session[SESSION_POLL_ENABLED] is True

    client.job = _job(id=client.job.id, status="running", started_at=_NOW, processed_rows=3)
    snap, err = fetch_and_store_job_status(client, client.job.id)  # type: ignore[arg-type]
    assert err is None
    assert snap is not None
    assert snap["status"] == "running"
    assert snap["processed_rows"] == 3
    assert session[SESSION_POLL_ENABLED] is True


def test_fetch_completed_and_failed_stop_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {SESSION_POLL_ENABLED: True}
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    completed = _job(
        status="completed",
        total_rows=10,
        processed_rows=10,
        success_count=8,
        failed_count=2,
        finished_at=_NOW,
    )
    client = RecordingClient(completed)
    snap, err = fetch_and_store_job_status(client, completed.id)  # type: ignore[arg-type]
    assert err is None
    assert snap is not None
    assert snap["success_count"] == 8
    assert snap["failed_count"] == 2
    assert session[SESSION_POLL_ENABLED] is False
    assert is_terminal_status(session[SESSION_LAST_STATUS])

    failed = _job(id="fail-1", status="failed", error_message="boom", finished_at=_NOW)
    session[SESSION_POLL_ENABLED] = True
    client = RecordingClient(failed)
    snap, err = fetch_and_store_job_status(client, failed.id)  # type: ignore[arg-type]
    assert err is None
    assert snap is not None
    assert snap["error_message"] == "boom"
    assert session[SESSION_POLL_ENABLED] is False


def test_fetch_preserves_job_id_on_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {
        SESSION_JOB_ID: "keep-me",
        SESSION_JOB_SNAPSHOT: {"id": "keep-me", "status": "pending"},
    }
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    client = RecordingClient()
    client.get_error = ApiError("The API request timed out.", status_code=None)
    snap, err = fetch_and_store_job_status(client, "keep-me")  # type: ignore[arg-type]
    assert err is not None
    assert "timed out" in err.lower()
    assert session[SESSION_JOB_ID] == "keep-me"
    assert snap == {"id": "keep-me", "status": "pending"}
    assert session[SESSION_STATUS_ERROR]


def test_fetch_preserves_job_id_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {SESSION_JOB_ID: "keep-me"}
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    client = RecordingClient()
    client.get_error = ApiError(
        "Cannot reach the API at http://test.local. Confirm the backend is running.",
    )
    _snap, err = fetch_and_store_job_status(client, "keep-me")  # type: ignore[arg-type]
    assert err is not None
    assert "Cannot reach the API" in err
    assert session[SESSION_JOB_ID] == "keep-me"


def test_in_flight_guard_skips_overlapping_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {
        SESSION_IN_FLIGHT: True,
        SESSION_JOB_SNAPSHOT: {"id": "j1", "status": "running"},
    }
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    client = RecordingClient()
    snap, err = fetch_and_store_job_status(client, "j1")  # type: ignore[arg-type]
    assert err is None
    assert client.get_calls == []
    assert snap == {"id": "j1", "status": "running"}


def test_apply_job_snapshot_counts() -> None:
    # apply_job_snapshot needs session_state; covered via fetch tests.
    job = _job(total_rows=5, success_count=4, failed_count=1, status="completed")
    snap = job_snapshot_from_model(job)
    assert snap["total_rows"] == 5
    assert snap["success_count"] == 4
    assert snap["failed_count"] == 1


# --- AppTest UI tests -------------------------------------------------------


def test_page_renders_without_file() -> None:
    at = _run_page()
    assert not at.exception
    assert any("Bulk Import" in t.value for t in at.title)
    assert any("Select a CSV file" in i.value for i in at.info)


def test_employees_is_default_import_target() -> None:
    at = _run_page()
    assert not at.exception
    assert at.radio
    assert at.radio[0].value == "Employees"
    assert at.session_state[SESSION_IMPORT_TARGET] == TARGET_EMPLOYEES
    assert any("employee CSV" in c.value for c in at.caption)
    assert any("Employee Number" in m.value for m in at.markdown)


def test_course_target_shows_guidance_and_routes_upload() -> None:
    client = RecordingClient(_job(import_target="courses", filename="courses.csv"))
    at = _run_page(api_client=client)
    at.radio[0].set_value("Courses").run()
    assert at.session_state[SESSION_IMPORT_TARGET] == TARGET_COURSES
    assert any("course CSV" in c.value for c in at.caption)
    assert any("Course Code" in m.value for m in at.markdown)

    at.file_uploader[0].set_value(("courses.csv", _csv_bytes(rows=2), "text/csv")).run()
    at.button[0].click().run()
    assert client.upload_endpoints == ["/imports/courses"]
    assert len(client.upload_calls) == 1
    assert any("courses" in m.value for m in at.markdown)


def test_enrolment_target_shows_guidance_and_routes_upload() -> None:
    client = RecordingClient(_job(import_target="enrolments", filename="enrolments.csv"))
    at = _run_page(api_client=client)
    at.radio[0].set_value("Enrolments").run()
    assert at.session_state[SESSION_IMPORT_TARGET] == TARGET_ENROLMENTS
    assert any("enrolment CSV" in c.value for c in at.caption)
    assert any("Enrolment Date" in m.value for m in at.markdown)

    at.file_uploader[0].set_value(("enrolments.csv", _csv_bytes(rows=2), "text/csv")).run()
    at.button[0].click().run()
    assert client.upload_endpoints == ["/imports/enrolments"]
    assert len(client.upload_calls) == 1


def test_employee_target_routes_to_existing_upload() -> None:
    client = RecordingClient(_job())
    at = _run_page(api_client=client)
    at.radio[0].set_value("Courses").run()
    at.radio[0].set_value("Employees").run()
    assert at.session_state[SESSION_IMPORT_TARGET] == TARGET_EMPLOYEES
    at.file_uploader[0].set_value(("employees.csv", _csv_bytes(rows=2), "text/csv")).run()
    at.button[0].click().run()
    assert client.upload_endpoints == ["/imports"]


def test_page_valid_preview_without_upload() -> None:
    class GuardClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            raise AssertionError("upload must not run automatically")

        def upload_course_import(self, **kwargs):  # noqa: ANN003
            raise AssertionError("upload must not run automatically")

        def upload_enrolment_import(self, **kwargs):  # noqa: ANN003
            raise AssertionError("upload must not run automatically")

        def get_import_job(self, job_id: str) -> ImportJobRead:
            raise AssertionError("must not poll without Job ID")

    at = _run_page(api_client=GuardClient())
    at.file_uploader[0].set_value(("employees.csv", _csv_bytes(rows=12), "text/csv")).run()
    assert not at.exception
    assert at.dataframe
    assert at.button
    assert not at.success


def test_page_empty_csv_shows_error() -> None:
    at = _run_page()
    at.file_uploader[0].set_value(("empty.csv", b"", "text/csv")).run()
    assert not at.exception
    assert any("empty" in e.value.lower() for e in at.error)


def test_page_oversized_file_shows_error() -> None:
    oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
    at = _run_page()
    at.file_uploader[0].set_value(("big.csv", oversized, "text/csv")).run()
    assert not at.exception
    assert any("maximum allowed size" in e.value.lower() for e in at.error)


def test_upload_only_after_button_click() -> None:
    client = RecordingClient(_job())
    at = _run_page(api_client=client)
    content = _csv_bytes(rows=2)
    at.file_uploader[0].set_value(("employees.csv", content, "text/csv")).run()
    assert client.upload_calls == []

    at.button[0].click().run()
    assert len(client.upload_calls) == 1
    assert client.upload_calls[0]["filename"] == "employees.csv"
    assert client.upload_calls[0]["content"] == content
    assert any("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in m.value for m in at.markdown)


def test_upload_dry_run_checkbox() -> None:
    client = RecordingClient(_job(dry_run=True))
    at = _run_page(api_client=client)
    at.file_uploader[0].set_value(("batch.csv", _csv_bytes(), "text/csv")).run()
    at.checkbox[0].check().run()
    at.button[0].click().run()
    assert client.upload_calls[0]["dry_run"] is True
    assert any(
        "yes" in m.value.lower() and "dry" in m.value.lower() for m in at.markdown
    )


def test_successful_upload_shows_job_id_and_status() -> None:
    client = RecordingClient(_job(id="job-123", status="pending"))
    at = _run_page(api_client=client)
    _upload_csv(at)
    assert not at.exception
    assert any("Upload accepted" in s.value for s in at.success)
    assert any("job-123" in m.value for m in at.markdown)
    assert any("pending" in m.value for m in at.markdown)
    assert at.session_state[SESSION_JOB_ID] == "job-123"


def test_api_error_on_upload() -> None:
    class FakeClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            raise ApiError(
                "Only CSV uploads are supported",
                status_code=400,
                code="validation_error",
            )

        def get_import_job(self, job_id: str) -> ImportJobRead:
            raise AssertionError("no status fetch after failed upload")

    at = _run_page(api_client=FakeClient())
    at.file_uploader[0].set_value(("employees.csv", _csv_bytes(), "text/csv")).run()
    at.button[0].click().run()
    assert not at.exception
    assert any("Only CSV uploads are supported" in e.value for e in at.error)
    assert SESSION_JOB_ID not in at.session_state


def test_invalid_extension_helper_used_for_non_csv_name() -> None:
    message = validate_csv_selection(
        filename="notes.txt",
        size=5,
        content=b"hello",
    )
    assert message is not None
    assert "CSV" in message


def test_original_bytes_preserved_on_upload() -> None:
    original = (
        b"\xef\xbb\xbfEmployee Number,First Name,Last Name,Work Email,Department ID\n"
        b"E1,A,B,a@b.com,1\n"
    )
    client = RecordingClient()
    at = _run_page(api_client=client)
    at.file_uploader[0].set_value(("bom.csv", original, "text/csv")).run()
    at.button[0].click().run()
    assert client.upload_calls[0]["content"] == original


def test_no_status_polling_without_job_id() -> None:
    client = RecordingClient()
    at = _run_page(api_client=client)
    assert SESSION_JOB_ID not in at.session_state
    assert client.get_calls == []
    assert not any("Import job status" in t.value for t in at.subheader)


def test_job_id_persists_across_rerun_without_file() -> None:
    client = RecordingClient(_job(id="persist-1", status="pending"))
    at = _run_page(api_client=client)
    _upload_csv(at)
    assert at.session_state[SESSION_JOB_ID] == "persist-1"

    # Clear file selection; Job ID must remain and status section still render.
    at.file_uploader[0].set_value(None).run()
    assert at.session_state[SESSION_JOB_ID] == "persist-1"
    assert any("Import job status" in t.value for t in at.subheader)
    assert any("persist-1" in m.value for m in at.markdown)


def test_manual_refresh_status_button() -> None:
    job = _job(id="refresh-1", status="pending", total_rows=0)
    client = RecordingClient(job)
    at = _run_page(api_client=client)
    _upload_csv(at)
    initial_gets = len(client.get_calls)

    client.job = _job(
        id="refresh-1",
        status="running",
        total_rows=10,
        processed_rows=4,
        success_count=3,
        failed_count=1,
        started_at=_NOW,
    )
    # Refresh Status is the last button in the status panel.
    refresh_buttons = [b for b in at.button if "Refresh" in (b.label or "")]
    assert refresh_buttons, "Refresh Status button not found"
    refresh_buttons[0].click().run()
    assert len(client.get_calls) > initial_gets
    assert at.session_state[SESSION_LAST_STATUS] == "running"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Total rows") == 10 or str(metrics.get("Total rows")) == "10"
    assert str(metrics.get("Success")) == "3"
    assert str(metrics.get("Failed")) == "1"


def test_completed_status_shows_counts_and_stops_auto_poll() -> None:
    completed = _job(
        id="done-1",
        status="completed",
        total_rows=10,
        processed_rows=10,
        success_count=9,
        failed_count=1,
        finished_at=_NOW,
    )
    client = RecordingClient(completed)
    # Seed session as if upload already happened with polling on, then fetch completed.
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "done-1"
    at.session_state[SESSION_POLL_ENABLED] = True
    at.session_state[SESSION_LAST_STATUS] = "running"
    at.run()
    assert at.session_state[SESSION_LAST_STATUS] == "completed"
    assert at.session_state[SESSION_POLL_ENABLED] is False
    assert any("completed" in s.value.lower() for s in at.success) or any(
        "completed" in m.value for m in at.markdown
    )


def test_failed_status_display() -> None:
    failed = _job(
        id="fail-ui",
        status="failed",
        error_message="Uploaded file not found for import job",
        finished_at=_NOW,
    )
    client = RecordingClient(failed)
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "fail-ui"
    at.session_state[SESSION_POLL_ENABLED] = True
    at.session_state[SESSION_LAST_STATUS] = "running"
    at.run()
    assert at.session_state[SESSION_LAST_STATUS] == "failed"
    assert at.session_state[SESSION_POLL_ENABLED] is False
    assert any("failed" in e.value.lower() for e in at.error) or any(
        "failed" in m.value for m in at.markdown
    )


def test_status_api_error_keeps_job_id_in_ui() -> None:
    client = RecordingClient(_job(id="err-1", status="pending"))
    at = _run_page(api_client=client)
    _upload_csv(at)
    client.get_error = ApiError("The API request timed out. Confirm the backend is running.")
    refresh_buttons = [b for b in at.button if "Refresh" in (b.label or "")]
    refresh_buttons[0].click().run()
    assert at.session_state[SESSION_JOB_ID] == "err-1"
    assert any("timed out" in w.value.lower() for w in at.warning) or any(
        "timed out" in e.value.lower() for e in at.error
    )


def test_automatic_polling_uses_active_statuses() -> None:
    """Pending/running keep auto-poll enabled; completed/failed stop it."""
    assert should_auto_poll(job_id="x", status="pending", poll_enabled=True)
    assert should_auto_poll(job_id="x", status="running", poll_enabled=True)
    assert not should_auto_poll(job_id="x", status="completed", poll_enabled=True)
    assert not should_auto_poll(job_id="x", status="failed", poll_enabled=True)


# --- Phase 4.5: errors + retry ------------------------------------------------


def test_retry_eligibility_matches_backend_rules() -> None:
    assert is_retry_eligible(_job(status="failed").model_dump(mode="json"))
    assert is_retry_eligible(
        _job(status="completed", failed_count=2).model_dump(mode="json")
    )
    assert not is_retry_eligible(
        _job(status="completed", failed_count=0).model_dump(mode="json")
    )
    assert not is_retry_eligible(_job(status="pending").model_dump(mode="json"))
    assert not is_retry_eligible(_job(status="running").model_dump(mode="json"))
    assert not is_retry_eligible(
        _job(status="failed", dry_run=True).model_dump(mode="json")
    )


def test_should_fetch_errors_only_when_needed() -> None:
    assert should_fetch_import_errors(
        _job(status="failed").model_dump(mode="json")
    )
    assert should_fetch_import_errors(
        _job(status="completed", failed_count=1).model_dump(mode="json")
    )
    assert not should_fetch_import_errors(
        _job(status="completed", failed_count=0).model_dump(mode="json")
    )
    assert not should_fetch_import_errors(
        _job(status="pending").model_dump(mode="json")
    )


def test_error_list_to_rows_maps_schema_fields() -> None:
    payload = {
        "items": [
            {
                "id": 9,
                "row_number": 3,
                "employee_number": "E3",
                "field": "work_email",
                "code": "invalid_email",
                "message": "Invalid email",
            }
        ]
    }
    rows = error_list_to_rows(payload)
    assert rows == [
        {
            "row_number": 3,
            "employee_number": "E3",
            "record_key": None,
            "field": "work_email",
            "code": "invalid_email",
            "message": "Invalid email",
        }
    ]


def test_error_list_to_rows_includes_record_key_when_present() -> None:
    payload = {
        "items": [
            {
                "id": 1,
                "row_number": 2,
                "employee_number": None,
                "record_key": "C101|E55",
                "field": "course_code",
                "code": "unknown_course",
                "message": "Course not found",
            }
        ]
    }
    rows = error_list_to_rows(payload)
    assert rows[0]["record_key"] == "C101|E55"
    assert rows[0]["employee_number"] is None


def test_normalize_import_target_defaults_to_employees() -> None:
    assert normalize_import_target(None) == TARGET_EMPLOYEES
    assert normalize_import_target("COURSES") == TARGET_COURSES
    assert normalize_import_target("enrolments") == TARGET_ENROLMENTS
    assert normalize_import_target("unknown") == TARGET_EMPLOYEES


def test_target_guidance_is_target_specific() -> None:
    employees = target_guidance(TARGET_EMPLOYEES)
    courses = target_guidance(TARGET_COURSES)
    enrolments = target_guidance(TARGET_ENROLMENTS)
    assert "employee" in employees["caption"].lower()
    assert "course" in courses["caption"].lower()
    assert "enrolment" in enrolments["caption"].lower()
    assert "Course Code" in courses["columns"]
    assert "Employee Number" in enrolments["columns"]
    assert "do not insert employees" in employees["dry_run_label"]
    assert "do not insert courses" in courses["dry_run_label"]
    assert "do not insert enrolments" in enrolments["dry_run_label"]


def test_upload_for_target_routes_to_matching_client_method() -> None:
    client = RecordingClient(_job())
    content = b"a,b\n1,2\n"
    upload_for_target(
        client, TARGET_EMPLOYEES, filename="e.csv", content=content, dry_run=False
    )
    upload_for_target(
        client, TARGET_COURSES, filename="c.csv", content=content, dry_run=True
    )
    upload_for_target(
        client, TARGET_ENROLMENTS, filename="n.csv", content=content, dry_run=False
    )
    assert client.upload_endpoints == [
        "/imports",
        "/imports/courses",
        "/imports/enrolments",
    ]
    assert client.upload_calls[1]["dry_run"] is True
    assert client.upload_calls[1]["filename"] == "c.csv"


def test_fetch_errors_pagination_and_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {}
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    client = RecordingClient(_job(id="err-job"))
    client.error_payload = _error_list(
        "err-job",
        page=2,
        page_size=1,
        total=2,
        items=[
            {
                "id": 2,
                "row_number": 5,
                "employee_number": None,
                "field": "department_id",
                "code": "missing_required",
                "message": "Missing department",
            }
        ],
    )
    payload, err = fetch_and_store_import_errors(
        client,  # type: ignore[arg-type]
        "err-job",
        page=2,
        page_size=1,
    )
    assert err is None
    assert payload is not None
    assert payload["page"] == 2
    assert payload["total"] == 2
    assert client.error_calls[-1] == {"job_id": "err-job", "page": 2, "page_size": 1}

    client.error_payload = _error_list("err-job", page=1, page_size=20, total=0, items=[])
    payload, err = fetch_and_store_import_errors(
        client,  # type: ignore[arg-type]
        "err-job",
        page=1,
        page_size=20,
    )
    assert err is None
    assert payload is not None
    assert payload["total"] == 0
    assert payload["items"] == []


def test_fetch_errors_api_failure_preserves_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    session: dict[str, Any] = {SESSION_JOB_ID: "keep-errors"}
    monkeypatch.setattr("streamlit_app.pages.bulk_import.st.session_state", session)

    client = RecordingClient()
    client.list_errors_error = ApiError("Cannot reach the API at http://test.local.")
    _payload, err = fetch_and_store_import_errors(
        client,  # type: ignore[arg-type]
        "keep-errors",
        page=1,
        page_size=20,
    )
    assert err is not None
    assert session[SESSION_JOB_ID] == "keep-errors"
    assert session[SESSION_ERROR_FETCH_ERROR]


def test_error_listing_ui_shows_rows_and_pagination() -> None:
    job = _job(id="partial-1", status="completed", failed_count=2, total_rows=5)
    client = RecordingClient(job)
    client.error_payload = _error_list(
        "partial-1",
        page=1,
        page_size=20,
        total=2,
        items=[
            {
                "id": 1,
                "row_number": 2,
                "employee_number": "E2",
                "field": "work_email",
                "code": "invalid_email",
                "message": "Invalid email",
            },
            {
                "id": 2,
                "row_number": 4,
                "employee_number": "E4",
                "field": None,
                "code": "duplicate_in_file",
                "message": "Duplicate employee number",
            },
        ],
    )
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "partial-1"
    at.session_state[SESSION_JOB_SNAPSHOT] = job.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "completed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.run()
    assert client.error_calls
    assert any("Import errors" in t.value for t in at.subheader)
    assert at.dataframe
    assert any("2 error" in c.value for c in at.caption)


def test_empty_error_results_ui() -> None:
    job = _job(id="failed-empty", status="failed", failed_count=0)
    client = RecordingClient(job)
    client.error_payload = _error_list("failed-empty", total=0, items=[])
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "failed-empty"
    at.session_state[SESSION_JOB_SNAPSHOT] = job.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "failed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.run()
    assert any("No import row errors" in i.value for i in at.info)


def test_error_api_failure_ui_keeps_job_id() -> None:
    job = _job(id="err-ui", status="failed", failed_count=1)
    client = RecordingClient(job)
    client.list_errors_error = ApiError("The API request timed out.")
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "err-ui"
    at.session_state[SESSION_JOB_SNAPSHOT] = job.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "failed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.run()
    assert at.session_state[SESSION_JOB_ID] == "err-ui"
    assert any("timed out" in w.value.lower() for w in at.warning) or any(
        "timed out" in e.value.lower() for e in at.error
    )


def test_retry_hidden_when_not_eligible() -> None:
    job = _job(id="ok-1", status="completed", failed_count=0)
    client = RecordingClient(job)
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "ok-1"
    at.session_state[SESSION_JOB_SNAPSHOT] = job.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "completed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.run()
    assert not any(b.label == "Retry import" for b in at.button)
    assert any("cannot be retried" in c.value.lower() for c in at.caption)


def test_retry_requires_confirmation() -> None:
    job = _job(id="retry-parent", status="completed", failed_count=3)
    client = RecordingClient(job)
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "retry-parent"
    at.session_state[SESSION_JOB_SNAPSHOT] = job.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "completed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.run()
    retry_buttons = [b for b in at.button if b.label == "Retry import"]
    assert retry_buttons
    assert retry_buttons[0].disabled
    assert client.retry_calls == []


def test_retry_success_tracks_new_job_id() -> None:
    parent = _job(id="retry-parent", status="failed", failed_count=1)
    child = _job(id="retry-child", status="pending", parent_job_id="retry-parent")
    client = RecordingClient(parent)
    client.retry_result = child
    client.job = parent
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "retry-parent"
    at.session_state[SESSION_SOURCE_JOB_ID] = "retry-parent"
    at.session_state[SESSION_JOB_SNAPSHOT] = parent.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "failed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.run()

    # Confirm then submit.
    confirm = [c for c in at.checkbox if "confirm" in (c.label or "").lower()]
    assert confirm
    confirm[0].check().run()
    client.job = child  # subsequent status fetches return child
    retry_buttons = [b for b in at.button if b.label == "Retry import"]
    retry_buttons[0].click().run()

    assert client.retry_calls == ["retry-parent"]
    assert at.session_state[SESSION_JOB_ID] == "retry-child"
    assert at.session_state[SESSION_SOURCE_JOB_ID] == "retry-parent"
    assert at.session_state[SESSION_LAST_STATUS] == "pending"
    assert at.session_state[SESSION_POLL_ENABLED] is True
    assert at.session_state.get(SESSION_RETRY_IN_FLIGHT) is False


def test_duplicate_retry_prevention() -> None:
    job = _job(id="dup-parent", status="completed", failed_count=1)
    client = RecordingClient(job)
    at = _run_page(api_client=client)
    at.session_state[SESSION_JOB_ID] = "dup-parent"
    at.session_state[SESSION_JOB_SNAPSHOT] = job.model_dump(mode="json")
    at.session_state[SESSION_LAST_STATUS] = "completed"
    at.session_state[SESSION_POLL_ENABLED] = False
    at.session_state[SESSION_RETRY_IN_FLIGHT] = True
    at.run()
    assert any("already submitted" in w.value.lower() for w in at.warning)
    assert client.retry_calls == []
    assert not any(b.label == "Retry import" for b in at.button)
