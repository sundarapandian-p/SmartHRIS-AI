"""Bulk Import page — CSV upload UI (status polling / retry come later)."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from streamlit_app.api_client import ApiError, SmartHrisApiClient
from streamlit_app.constants import NAV_BULK_IMPORT

# Matches Settings.max_upload_bytes default (50 MiB).
MAX_UPLOAD_BYTES = 52_428_800
PREVIEW_ROW_LIMIT = 10

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
        # Detach so closing the wrapper does not close shared buffers unexpectedly.
        try:
            text.detach()
        except Exception:
            pass

    return CsvPreview(headers=headers, rows=rows, truncated=truncated)


def _status_label(status: object) -> str:
    value = getattr(status, "value", status)
    return str(value)


def render(*, api_client: SmartHrisApiClient | None = None) -> None:
    """Render the Bulk Import upload form (no automatic upload on file select)."""
    st.title(NAV_BULK_IMPORT)
    st.caption(
        "Upload an employee CSV to create an import job. "
        "Job status polling, error review, and retry are not part of this page yet."
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
        return

    filename = uploaded.name or ""
    size = int(getattr(uploaded, "size", 0) or 0)

    if size > MAX_UPLOAD_BYTES:
        st.error(
            f"File exceeds the maximum allowed size of {MAX_UPLOAD_BYTES} bytes (50 MiB)."
        )
        return

    # Preserve original bytes exactly for the API (no re-encoding / rewriting).
    content = uploaded.getvalue()
    local_error = validate_csv_selection(filename=filename, size=size, content=content)
    if local_error:
        st.error(local_error)
        return

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
                    preview.headers[i] if i < len(preview.headers) else f"column_{i}": (
                        row[i] if i < len(row) else ""
                    )
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

    upload_clicked = st.button("Upload CSV", type="primary", key="bulk_import_upload")
    if not upload_clicked:
        return

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
        return
    except Exception:
        progress.empty()
        st.error("Upload failed due to an unexpected client error. Please try again.")
        return

    st.success("Upload accepted. Import job created.")
    st.markdown(f"**Import Job ID:** `{job.id}`")
    st.markdown(f"**Status:** `{_status_label(job.status)}`")
    st.markdown(f"**Dry run:** `{'yes' if job.dry_run else 'no'}`")
    st.caption(
        "Refresh or use a later status page to follow processing. "
        "This page does not poll job status."
    )


# Streamlit page runner executes this file as __main__.
if __name__ == "__main__":
    render()
