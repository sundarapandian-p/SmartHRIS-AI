"""Bulk Import page — CSV upload + import job status polling."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.auth import get_api_client, require_page_access
from streamlit_app.constants import NAV_BULK_IMPORT

# Matches Settings.max_upload_bytes default (50 MiB).
MAX_UPLOAD_BYTES = 52_428_800
PREVIEW_ROW_LIMIT = 10

TARGET_EMPLOYEES = "employees"
TARGET_COURSES = "courses"
TARGET_ENROLMENTS = "enrolments"
IMPORT_TARGETS = (TARGET_EMPLOYEES, TARGET_COURSES, TARGET_ENROLMENTS)
TARGET_LABELS = {
    TARGET_EMPLOYEES: "Employees",
    TARGET_COURSES: "Courses",
    TARGET_ENROLMENTS: "Enrolments",
}

SESSION_IMPORT_TARGET = "bulk_import_target"

# Backend ImportJobStatus values (no separate "processing" state — use running).
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

ACTIVE_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING})
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})

POLL_INTERVAL_SECONDS = 4

SESSION_JOB_ID = "bulk_import_job_id"
SESSION_POLL_ENABLED = "bulk_import_poll_enabled"
SESSION_LAST_STATUS = "bulk_import_last_status"
SESSION_JOB_SNAPSHOT = "bulk_import_job_snapshot"
SESSION_STATUS_ERROR = "bulk_import_status_error"
SESSION_IN_FLIGHT = "bulk_import_status_in_flight"
SESSION_SOURCE_JOB_ID = "bulk_import_source_job_id"
SESSION_ERROR_PAGE = "bulk_import_error_page"
SESSION_ERROR_PAGE_SIZE = "bulk_import_error_page_size"
SESSION_ERROR_SNAPSHOT = "bulk_import_error_snapshot"
SESSION_ERROR_FETCH_ERROR = "bulk_import_error_fetch_error"
SESSION_RETRY_IN_FLIGHT = "bulk_import_retry_in_flight"

DEFAULT_ERROR_PAGE_SIZE = 20


# Preview dialect aligned with backend ``smarthris_csv`` (strict quoting).
class _PreviewCsvDialect(csv.Dialect):
    delimiter = ","
    quotechar = '"'
    escapechar = None
    doublequote = True
    skipinitialspace = False
    lineterminator = "\r\n"
    quoting = csv.QUOTE_MINIMAL
    strict = True


csv.register_dialect("smarthris_ui_preview_csv", _PreviewCsvDialect)


@dataclass(frozen=True, slots=True)
class CsvPreview:
    """Small CSV sample for UI display (not a full-file DataFrame)."""

    headers: list[str]
    rows: list[list[str]]
    truncated: bool


class CsvPreviewError(Exception):
    """Preview-only failure; original bytes are left unchanged for upload."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def is_csv_filename(filename: str) -> bool:
    """Case-insensitive ``.csv`` check (aligned with backend ``assert_csv_filename``)."""
    return Path(filename).suffix.lower() == ".csv"


def validate_csv_selection(
    *,
    filename: str | None,
    size: int,
    content: bytes,
) -> str | None:
    """
    Local pre-upload checks mirroring backend hard limits.

    Returns a user-facing error message, or ``None`` when checks pass.
    Header/column business rules remain on the API.
    """
    if filename is None or not str(filename).strip():
        return "Uploaded file must include a filename."
    if not is_csv_filename(filename):
        return "Only CSV uploads are supported. Please select a .csv file."
    if size <= 0 or len(content) == 0:
        return "The selected file is empty. Choose a CSV with a header row and data."
    if size > MAX_UPLOAD_BYTES or len(content) > MAX_UPLOAD_BYTES:
        return (
            f"File exceeds the maximum allowed size of {MAX_UPLOAD_BYTES} bytes (50 MiB)."
        )
    return None


def preview_csv(content: bytes, *, max_rows: int = PREVIEW_ROW_LIMIT) -> CsvPreview:
    """
    Stream a header + up to ``max_rows`` data rows for display.

    Uses UTF-8 with BOM (``utf-8-sig``). Does not build a full-file DataFrame.
    Does not mutate ``content``.
    """
    text = io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="")
    reader = csv.reader(text, dialect="smarthris_ui_preview_csv")
    try:
        try:
            header_cells = next(reader)
        except StopIteration as exc:
            raise CsvPreviewError("CSV file is empty; expected a header row.") from exc
        except csv.Error as exc:
            raise CsvPreviewError(f"Malformed CSV near header: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise CsvPreviewError(
                "Could not decode the file as UTF-8. Save the CSV as UTF-8 and try again."
            ) from exc

        headers = [cell.strip() for cell in header_cells]
        rows: list[list[str]] = []
        truncated = False
        try:
            for index, row in enumerate(reader):
                if index >= max_rows:
                    truncated = True
                    break
                rows.append(list(row))
        except csv.Error as exc:
            raise CsvPreviewError(
                f"Malformed CSV while reading preview rows: {exc}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise CsvPreviewError(
                "Could not decode the file as UTF-8. Save the CSV as UTF-8 and try again."
            ) from exc
    finally:
        try:
            text.detach()
        except Exception:
            pass

    return CsvPreview(headers=headers, rows=rows, truncated=truncated)


def normalize_status(status: object) -> str:
    """Return the backend status string (``pending`` / ``running`` / …)."""
    value = getattr(status, "value", status)
    return str(value)


def normalize_import_target(value: object | None) -> str:
    """Return a known import target; default to employees."""
    raw = getattr(value, "value", value)
    text = str(raw or TARGET_EMPLOYEES).strip().lower()
    return text if text in IMPORT_TARGETS else TARGET_EMPLOYEES


def target_guidance(target: str) -> dict[str, str]:
    """Target-specific copy for captions, uploader, and dry-run."""
    target = normalize_import_target(target)
    if target == TARGET_COURSES:
        return {
            "caption": (
                "Upload a course CSV to create an import job, follow status, "
                "review row errors, and retry eligible failed/partial imports."
            ),
            "columns": (
                "The API expects CSV columns such as **Course Code**, **Course Name**, "
                "**Category**, **Duration Hours**, and **Status** "
                "(aliases like `course_id` / `title` are accepted by the backend). "
                "Excel (`.xlsx`) is not supported here."
            ),
            "uploader_label": "Course CSV file",
            "dry_run_label": "Dry run (validate only; do not insert courses)",
            "dry_run_caption": "Dry run: yes (validate only; no course inserts).",
        }
    if target == TARGET_ENROLMENTS:
        return {
            "caption": (
                "Upload an enrolment CSV to create an import job, follow status, "
                "review row errors, and retry eligible failed/partial imports. "
                "Employees and courses must already exist."
            ),
            "columns": (
                "The API expects CSV columns such as **Employee Number**, **Course Code**, "
                "**Enrolment Date**, **Status**, optional **Completion Date**, and optional "
                "**Score**. Excel (`.xlsx`) is not supported here."
            ),
            "uploader_label": "Enrolment CSV file",
            "dry_run_label": "Dry run (validate only; do not insert enrolments)",
            "dry_run_caption": "Dry run: yes (validate only; no enrolment inserts).",
        }
    return {
        "caption": (
            "Upload an employee CSV to create an import job, follow status, "
            "review row errors, and retry eligible failed/partial imports."
        ),
        "columns": (
            "The API expects CSV columns such as **Employee Number**, **First Name**, "
            "**Last Name**, **Work Email**, and **Department ID** and/or **Department Name** "
            "(aliases like `employee_id` / `email` are accepted by the backend). "
            "Excel (`.xlsx`) is not supported here."
        ),
        "uploader_label": "Employee CSV file",
        "dry_run_label": "Dry run (validate only; do not insert employees)",
        "dry_run_caption": "Dry run: yes (validate only; no employee inserts).",
    }


def upload_for_target(
    client: SmartHrisApiClient,
    target: str,
    *,
    filename: str,
    content: bytes,
    dry_run: bool,
) -> Any:
    """Dispatch multipart upload to the matching API client method."""
    target = normalize_import_target(target)
    kwargs = {"filename": filename, "content": content, "dry_run": dry_run}
    if target == TARGET_COURSES:
        return client.upload_course_import(**kwargs)
    if target == TARGET_ENROLMENTS:
        return client.upload_enrolment_import(**kwargs)
    return client.upload_import(**kwargs)


def is_terminal_status(status: object) -> bool:
    return normalize_status(status) in TERMINAL_STATUSES


def is_active_status(status: object) -> bool:
    return normalize_status(status) in ACTIVE_STATUSES


def should_auto_poll(
    *,
    job_id: str | None,
    status: str | None,
    poll_enabled: bool,
) -> bool:
    """True only when a Job ID exists, polling is enabled, and status is non-terminal."""
    if not job_id or not poll_enabled:
        return False
    if status is None:
        return True
    return status in ACTIVE_STATUSES


def is_retry_eligible(snapshot: dict[str, Any] | None) -> bool:
    """
    Mirror backend ``_retry_eligibility_conflict`` (UI gate only; API is authoritative).

    Eligible: non-dry-run ``failed``, or ``completed`` with ``failed_count > 0``.
    """
    if not snapshot:
        return False
    if bool(snapshot.get("dry_run")):
        return False
    status = normalize_status(snapshot.get("status"))
    failed_count = int(snapshot.get("failed_count") or 0)
    if status == STATUS_FAILED:
        return True
    if status == STATUS_COMPLETED and failed_count > 0:
        return True
    return False


def should_fetch_import_errors(snapshot: dict[str, Any] | None) -> bool:
    """Fetch row errors only for terminal jobs that may have persisted issues."""
    if not snapshot or not is_terminal_status(snapshot.get("status")):
        return False
    status = normalize_status(snapshot.get("status"))
    failed_count = int(snapshot.get("failed_count") or 0)
    if status == STATUS_FAILED:
        return True
    return failed_count > 0


def format_timestamp(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def job_snapshot_from_model(job: Any) -> dict[str, Any]:
    """Store API job payload in session state as plain JSON-friendly data."""
    if hasattr(job, "model_dump"):
        return job.model_dump(mode="json")
    if isinstance(job, dict):
        return dict(job)
    raise TypeError("Unsupported job payload type")


def apply_job_snapshot(snapshot: dict[str, Any]) -> None:
    previous_id = None
    previous = st.session_state.get(SESSION_JOB_SNAPSHOT)
    if isinstance(previous, dict):
        previous_id = previous.get("id")
    st.session_state[SESSION_JOB_SNAPSHOT] = snapshot
    st.session_state[SESSION_LAST_STATUS] = normalize_status(snapshot.get("status"))
    st.session_state[SESSION_STATUS_ERROR] = None
    if is_terminal_status(snapshot.get("status")):
        st.session_state[SESSION_POLL_ENABLED] = False
    else:
        st.session_state[SESSION_POLL_ENABLED] = True
    # Reset error pagination/cache when the tracked job changes.
    if snapshot.get("id") != previous_id:
        st.session_state[SESSION_ERROR_PAGE] = 1
        st.session_state.pop(SESSION_ERROR_SNAPSHOT, None)
        st.session_state.pop(SESSION_ERROR_FETCH_ERROR, None)


def fetch_and_store_job_status(
    client: SmartHrisApiClient,
    job_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Call ``get_import_job`` and update session snapshot.

    Preserves ``SESSION_JOB_ID`` on failure. Returns ``(snapshot, error_message)``.
    """
    if not job_id:
        return None, "No import Job ID is available."
    if st.session_state.get(SESSION_IN_FLIGHT):
        return st.session_state.get(SESSION_JOB_SNAPSHOT), None

    st.session_state[SESSION_IN_FLIGHT] = True
    try:
        job = client.get_import_job(job_id)
        snapshot = job_snapshot_from_model(job)
        apply_job_snapshot(snapshot)
        # Ensure Job ID stays set even if API returns the same id.
        st.session_state[SESSION_JOB_ID] = str(snapshot.get("id") or job_id)
        return snapshot, None
    except ApiError as exc:
        st.session_state[SESSION_STATUS_ERROR] = str(exc)
        # Explicitly keep Job ID for retry / manual refresh.
        st.session_state[SESSION_JOB_ID] = job_id
        return st.session_state.get(SESSION_JOB_SNAPSHOT), str(exc)
    finally:
        st.session_state[SESSION_IN_FLIGHT] = False


def _display_job_snapshot(snapshot: dict[str, Any]) -> None:
    status = normalize_status(snapshot.get("status"))
    target = normalize_import_target(snapshot.get("import_target"))
    guidance = target_guidance(target)
    st.markdown(f"**Import Job ID:** `{snapshot.get('id', '—')}`")
    st.markdown(f"**Status:** `{status}`")
    st.markdown(f"**Import target:** `{target}`")
    parent_job_id = snapshot.get("parent_job_id")
    if parent_job_id:
        st.caption(f"Parent job (retry source): `{parent_job_id}`")
    source_job_id = st.session_state.get(SESSION_SOURCE_JOB_ID)
    if source_job_id and source_job_id != snapshot.get("id"):
        st.caption(f"Original uploaded job: `{source_job_id}`")
    cols = st.columns(4)
    cols[0].metric("Total rows", snapshot.get("total_rows", 0))
    cols[1].metric("Processed", snapshot.get("processed_rows", 0))
    cols[2].metric("Success", snapshot.get("success_count", 0))
    cols[3].metric("Failed", snapshot.get("failed_count", 0))

    st.caption(
        f"Created: `{format_timestamp(snapshot.get('created_at'))}` · "
        f"Started: `{format_timestamp(snapshot.get('started_at'))}` · "
        f"Finished: `{format_timestamp(snapshot.get('finished_at'))}`"
    )
    if snapshot.get("dry_run"):
        st.caption(guidance["dry_run_caption"])
    error_message = snapshot.get("error_message")
    if error_message:
        st.error(f"Job error: {error_message}")
    if status == STATUS_COMPLETED:
        st.success("Import job completed.")
    elif status == STATUS_FAILED:
        st.error("Import job failed.")
    elif status == STATUS_RUNNING:
        st.info("Import job is running…")
    elif status == STATUS_PENDING:
        st.info("Import job is pending…")


def error_list_to_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Map API error items to a compact table (actual schema fields only)."""
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "row_number": item.get("row_number"),
                "employee_number": item.get("employee_number"),
                "record_key": item.get("record_key"),
                "field": item.get("field"),
                "code": item.get("code"),
                "message": item.get("message"),
            }
        )
    return rows


def fetch_and_store_import_errors(
    client: SmartHrisApiClient,
    job_id: str,
    *,
    page: int,
    page_size: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """GET /imports/{id}/errors — preserves Job ID on failure."""
    if not job_id:
        return None, "No import Job ID is available."
    try:
        result = client.list_import_errors(job_id, page=page, page_size=page_size)
        payload = result.model_dump(mode="json")
        st.session_state[SESSION_ERROR_SNAPSHOT] = payload
        st.session_state[SESSION_ERROR_FETCH_ERROR] = None
        st.session_state[SESSION_JOB_ID] = job_id
        return payload, None
    except ApiError as exc:
        st.session_state[SESSION_ERROR_FETCH_ERROR] = str(exc)
        st.session_state[SESSION_JOB_ID] = job_id
        return st.session_state.get(SESSION_ERROR_SNAPSHOT), str(exc)


def _render_error_report(
    *,
    api_client: SmartHrisApiClient | None,
    snapshot: dict[str, Any],
) -> None:
    st.divider()
    st.subheader("Import errors")

    job_id = str(snapshot.get("id") or st.session_state.get(SESSION_JOB_ID) or "")
    if not job_id:
        return

    if not should_fetch_import_errors(snapshot):
        st.info("No import row errors for this job.")
        return

    page = int(st.session_state.get(SESSION_ERROR_PAGE) or 1)
    page_size = int(st.session_state.get(SESSION_ERROR_PAGE_SIZE) or DEFAULT_ERROR_PAGE_SIZE)

    controls = st.columns([1, 1, 1])
    with controls[0]:
        page = st.number_input(
            "Page",
            min_value=1,
            value=max(page, 1),
            step=1,
            key="bulk_import_error_page_input",
        )
    with controls[1]:
        page_size = st.selectbox(
            "Page size",
            options=[10, 20, 50, 100],
            index=[10, 20, 50, 100].index(page_size)
            if page_size in {10, 20, 50, 100}
            else 1,
            key="bulk_import_error_page_size_input",
        )
    with controls[2]:
        reload_clicked = st.button("Reload errors", key="bulk_import_reload_errors")

    st.session_state[SESSION_ERROR_PAGE] = int(page)
    st.session_state[SESSION_ERROR_PAGE_SIZE] = int(page_size)

    cached = st.session_state.get(SESSION_ERROR_SNAPSHOT)
    cache_matches = (
        isinstance(cached, dict)
        and cached.get("import_job_id") == job_id
        and int(cached.get("page") or 0) == int(page)
        and int(cached.get("page_size") or 0) == int(page_size)
    )
    if reload_clicked or not cache_matches:
        client = api_client if api_client is not None else get_api_client()
        _payload, error = fetch_and_store_import_errors(
            client,
            job_id,
            page=int(page),
            page_size=int(page_size),
        )
        if error:
            st.warning(f"Could not load import errors: {error}")

    payload = st.session_state.get(SESSION_ERROR_SNAPSHOT)
    fetch_error = st.session_state.get(SESSION_ERROR_FETCH_ERROR)
    if not payload:
        if fetch_error:
            st.error(fetch_error)
        else:
            st.info("No import row errors returned.")
        return

    total = int(payload.get("total") or 0)
    pages = int(payload.get("pages") or 0)
    st.caption(
        f"Job `{payload.get('import_job_id', job_id)}` — "
        f"{total} error(s), page {payload.get('page', page)} of {pages or 1}"
    )
    rows = error_list_to_rows(payload)
    if not rows:
        st.info("No import row errors on this page.")
        return
    st.dataframe(rows, width="stretch", hide_index=True)


def _render_retry_section(
    *,
    api_client: SmartHrisApiClient | None,
    snapshot: dict[str, Any],
) -> None:
    st.divider()
    st.subheader("Retry import")

    if not is_retry_eligible(snapshot):
        status = normalize_status(snapshot.get("status"))
        if snapshot.get("dry_run"):
            st.caption("Dry-run jobs cannot be retried.")
        elif status in ACTIVE_STATUSES:
            st.caption("Retry is available after the job finishes.")
        elif status == STATUS_COMPLETED and int(snapshot.get("failed_count") or 0) == 0:
            st.caption("Successful imports (zero failed rows) cannot be retried.")
        else:
            st.caption("This job cannot be retried.")
        return

    job_id = str(snapshot.get("id") or "")
    st.markdown(
        "Creates a **new child import job** that reprocesses this job's stored CSV. "
        "The original Job ID is kept for reference; tracking switches to the child."
    )

    if st.session_state.get(SESSION_RETRY_IN_FLIGHT):
        st.warning("A retry request was already submitted. Wait for the new job to appear.")
        return

    confirmed = st.checkbox(
        "I confirm I want to retry this import",
        value=False,
        key="bulk_import_retry_confirm",
    )
    retry_clicked = st.button(
        "Retry import",
        type="secondary",
        key="bulk_import_retry_submit",
        disabled=not confirmed,
    )
    if not retry_clicked:
        return
    if not confirmed:
        st.warning("Confirm retry before submitting.")
        return

    st.session_state[SESSION_RETRY_IN_FLIGHT] = True
    client = api_client if api_client is not None else get_api_client()
    try:
        child = client.retry_import(job_id)
    except ApiError as exc:
        st.session_state[SESSION_RETRY_IN_FLIGHT] = False
        st.error(str(exc))
        # Keep tracking the original job.
        st.session_state[SESSION_JOB_ID] = job_id
        return
    except Exception:
        st.session_state[SESSION_RETRY_IN_FLIGHT] = False
        st.error("Retry failed due to an unexpected client error.")
        st.session_state[SESSION_JOB_ID] = job_id
        return

    # Preserve original uploaded / parent relationship, then track the child.
    if not st.session_state.get(SESSION_SOURCE_JOB_ID):
        st.session_state[SESSION_SOURCE_JOB_ID] = job_id
    st.session_state[SESSION_JOB_ID] = child.id
    apply_job_snapshot(job_snapshot_from_model(child))
    st.session_state[SESSION_RETRY_IN_FLIGHT] = False
    st.success(
        f"Retry accepted. New Job ID: `{child.id}` "
        f"(parent: `{child.parent_job_id or job_id}`)."
    )
    st.rerun()


def _job_status_panel(
    *,
    api_client: SmartHrisApiClient | None,
    auto_fetch: bool,
) -> None:
    job_id = st.session_state.get(SESSION_JOB_ID)
    if not job_id:
        return

    st.markdown(f"**Tracking Job ID:** `{job_id}`")
    refresh_clicked = st.button("Refresh Status", key="bulk_import_refresh_status")

    should_fetch = auto_fetch or refresh_clicked or not st.session_state.get(
        SESSION_JOB_SNAPSHOT
    )
    if should_fetch:
        client = api_client if api_client is not None else get_api_client()
        _snapshot, error = fetch_and_store_job_status(client, str(job_id))
        if error:
            st.warning(f"Could not refresh status: {error}")

    err = st.session_state.get(SESSION_STATUS_ERROR)
    snapshot = st.session_state.get(SESSION_JOB_SNAPSHOT)
    if snapshot:
        _display_job_snapshot(snapshot)
    elif err:
        st.error(err)

    if auto_fetch and is_terminal_status(st.session_state.get(SESSION_LAST_STATUS)):
        # Stop automatic polling by remounting the static fragment on a full rerun.
        if st.session_state.get(SESSION_POLL_ENABLED):
            st.session_state[SESSION_POLL_ENABLED] = False
            st.rerun()


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def _polling_job_status_panel(api_client: SmartHrisApiClient | None = None) -> None:
    """Auto-refresh while the job is pending or running (every POLL_INTERVAL_SECONDS)."""
    _job_status_panel(api_client=api_client, auto_fetch=True)


@st.fragment
def _static_job_status_panel(api_client: SmartHrisApiClient | None = None) -> None:
    """Manual refresh only (terminal jobs, or polling disabled)."""
    _job_status_panel(api_client=api_client, auto_fetch=False)


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    """Render CSV upload controls and import job status (poll when non-terminal)."""
    require_page_access("bulk_import")
    st.title(NAV_BULK_IMPORT)

    if SESSION_IMPORT_TARGET not in st.session_state:
        st.session_state[SESSION_IMPORT_TARGET] = TARGET_EMPLOYEES

    selected_label = st.radio(
        "Import target",
        options=[TARGET_LABELS[t] for t in IMPORT_TARGETS],
        index=IMPORT_TARGETS.index(
            normalize_import_target(st.session_state[SESSION_IMPORT_TARGET])
        ),
        horizontal=True,
        key="bulk_import_target_radio",
        help="Employees remain the default. Courses and enrolments use dedicated API endpoints.",
    )
    label_to_target = {label: key for key, label in TARGET_LABELS.items()}
    selected_target = normalize_import_target(label_to_target.get(selected_label))
    st.session_state[SESSION_IMPORT_TARGET] = selected_target
    guidance = target_guidance(selected_target)

    st.caption(guidance["caption"])
    st.markdown(guidance["columns"])

    uploaded = st.file_uploader(
        guidance["uploader_label"],
        type=["csv"],
        accept_multiple_files=False,
        help=f"Maximum size: 50 MiB ({MAX_UPLOAD_BYTES:,} bytes). UTF-8 recommended.",
        key="bulk_import_csv",
    )

    if uploaded is None:
        st.info("Select a CSV file to preview. Upload runs only after you click the button.")
    else:
        filename = uploaded.name or ""
        size = int(getattr(uploaded, "size", 0) or 0)

        if size > MAX_UPLOAD_BYTES:
            st.error(
                f"File exceeds the maximum allowed size of {MAX_UPLOAD_BYTES} bytes (50 MiB)."
            )
        else:
            # Preserve original bytes exactly for the API (no re-encoding / rewriting).
            content = uploaded.getvalue()
            local_error = validate_csv_selection(
                filename=filename,
                size=size,
                content=content,
            )
            if local_error:
                st.error(local_error)
            else:
                st.write(f"**Selected file:** `{filename}` ({size:,} bytes)")

                try:
                    preview = preview_csv(content, max_rows=PREVIEW_ROW_LIMIT)
                except CsvPreviewError as exc:
                    st.warning(
                        f"Preview unavailable: {exc.message} "
                        "You may still upload; the API will validate the file."
                    )
                else:
                    st.subheader(f"Preview (first {PREVIEW_ROW_LIMIT} data rows)")
                    if not preview.headers:
                        st.warning("CSV header row is empty.")
                    else:
                        table_rows = [
                            {
                                preview.headers[i]
                                if i < len(preview.headers)
                                else f"column_{i}": (row[i] if i < len(row) else "")
                                for i in range(max(len(preview.headers), len(row)))
                            }
                            for row in preview.rows
                        ]
                        if table_rows:
                            st.dataframe(table_rows, width="stretch", hide_index=True)
                        else:
                            st.info("No data rows found after the header (header-only CSV).")
                        if preview.truncated:
                            st.caption(
                                f"Showing the first {PREVIEW_ROW_LIMIT} data rows only; "
                                "the full file is uploaded unchanged."
                            )

                dry_run = st.checkbox(
                    guidance["dry_run_label"],
                    value=False,
                    key="bulk_import_dry_run",
                )

                upload_clicked = st.button(
                    "Upload CSV",
                    type="primary",
                    key="bulk_import_upload",
                )
                if upload_clicked:
                    client = api_client if api_client is not None else get_api_client()
                    progress = st.progress(0, text="Uploading CSV to the API…")
                    try:
                        progress.progress(40, text="Uploading CSV to the API…")
                        job = upload_for_target(
                            client,
                            selected_target,
                            filename=filename,
                            content=content,
                            dry_run=dry_run,
                        )
                        progress.progress(100, text="Upload complete.")
                    except ApiError as exc:
                        progress.empty()
                        st.error(str(exc))
                    except Exception:
                        progress.empty()
                        st.error(
                            "Upload failed due to an unexpected client error. Please try again."
                        )
                    else:
                        st.session_state[SESSION_JOB_ID] = job.id
                        st.session_state[SESSION_SOURCE_JOB_ID] = job.id
                        st.session_state[SESSION_RETRY_IN_FLIGHT] = False
                        apply_job_snapshot(job_snapshot_from_model(job))
                        st.success("Upload accepted. Import job created.")
                        st.markdown(f"**Import Job ID:** `{job.id}`")
                        st.markdown(
                            f"**Import target:** "
                            f"`{normalize_import_target(getattr(job, 'import_target', selected_target))}`"
                        )
                        st.markdown(f"**Status:** `{normalize_status(job.status)}`")
                        st.markdown(f"**Dry run:** `{'yes' if job.dry_run else 'no'}`")

    job_id = st.session_state.get(SESSION_JOB_ID)
    if not job_id:
        return

    st.divider()
    st.subheader("Import job status")
    poll = should_auto_poll(
        job_id=str(job_id),
        status=st.session_state.get(SESSION_LAST_STATUS),
        poll_enabled=bool(st.session_state.get(SESSION_POLL_ENABLED, False)),
    )
    if poll:
        st.caption(
            f"Auto-refreshing every {POLL_INTERVAL_SECONDS}s while status is "
            f"`{STATUS_PENDING}` or `{STATUS_RUNNING}`."
        )
        _polling_job_status_panel(api_client)
    else:
        st.caption("Automatic polling is stopped for terminal jobs. Use Refresh Status to update.")
        _static_job_status_panel(api_client)

    snapshot = st.session_state.get(SESSION_JOB_SNAPSHOT)
    if isinstance(snapshot, dict) and is_terminal_status(snapshot.get("status")):
        _render_error_report(api_client=api_client, snapshot=snapshot)
        _render_retry_section(api_client=api_client, snapshot=snapshot)


# Streamlit page runner executes this file as __main__.
if __name__ == "__main__":
    render()
