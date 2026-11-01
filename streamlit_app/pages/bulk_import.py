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
from streamlit_app.constants import NAV_BULK_IMPORT

# Matches Settings.max_upload_bytes default (50 MiB).
MAX_UPLOAD_BYTES = 52_428_800
PREVIEW_ROW_LIMIT = 10

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
    st.session_state[SESSION_JOB_SNAPSHOT] = snapshot
    st.session_state[SESSION_LAST_STATUS] = normalize_status(snapshot.get("status"))
    st.session_state[SESSION_STATUS_ERROR] = None
    if is_terminal_status(snapshot.get("status")):
        st.session_state[SESSION_POLL_ENABLED] = False
    else:
        st.session_state[SESSION_POLL_ENABLED] = True


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
    st.markdown(f"**Import Job ID:** `{snapshot.get('id', '—')}`")
    st.markdown(f"**Status:** `{status}`")
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
        st.caption("Dry run: yes (validate only; no employee inserts).")
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
        client = api_client if api_client is not None else SmartHrisApiClient()
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
    st.title(NAV_BULK_IMPORT)
    st.caption(
        "Upload an employee CSV to create an import job, then follow status here. "
        "Error review and retry are not part of this page yet."
    )
    st.markdown(
        "The API expects CSV columns such as **Employee Number**, **First Name**, "
        "**Last Name**, **Work Email**, and **Department ID** and/or **Department Name** "
        "(aliases like `employee_id` / `email` are accepted by the backend). "
        "Excel (`.xlsx`) is not supported here."
    )

    uploaded = st.file_uploader(
        "Employee CSV file",
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
                    "Dry run (validate only; do not insert employees)",
                    value=False,
                    key="bulk_import_dry_run",
                )

                upload_clicked = st.button(
                    "Upload CSV",
                    type="primary",
                    key="bulk_import_upload",
                )
                if upload_clicked:
                    client = api_client if api_client is not None else SmartHrisApiClient()
                    progress = st.progress(0, text="Uploading CSV to the API…")
                    try:
                        progress.progress(40, text="Uploading CSV to the API…")
                        job = client.upload_import(
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
                        apply_job_snapshot(job_snapshot_from_model(job))
                        st.success("Upload accepted. Import job created.")
                        st.markdown(f"**Import Job ID:** `{job.id}`")
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


# Streamlit page runner executes this file as __main__.
if __name__ == "__main__":
    render()
