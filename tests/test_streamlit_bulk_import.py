"""Phase 4.3 — Bulk Import page tests (no live API or MySQL)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from streamlit.testing.v1 import AppTest

from smarthris.schemas.import_job import ImportJobRead
from streamlit_app.api_client import ApiError
from streamlit_app.pages.bulk_import import (
    MAX_UPLOAD_BYTES,
    PREVIEW_ROW_LIMIT,
    CsvPreviewError,
    preview_csv,
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


def _run_page(api_client=None) -> AppTest:
    """Drive ``render`` via AppTest (function body must be import-complete)."""

    def _script(client=None) -> None:
        from streamlit_app.pages.bulk_import import render as render_bulk_import

        render_bulk_import(api_client=client)

    return AppTest.from_function(
        _script,
        default_timeout=_TIMEOUT,
        kwargs={"client": api_client},
    ).run()


# --- Pure helper unit tests -------------------------------------------------


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


# --- AppTest UI tests -------------------------------------------------------


def test_page_renders_without_file() -> None:
    at = _run_page()
    assert not at.exception
    assert any("Bulk Import" in t.value for t in at.title)
    assert any("Select a CSV file" in i.value for i in at.info)


def test_page_valid_preview_without_upload() -> None:
    class GuardClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            raise AssertionError("upload must not run automatically")

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
    calls: list[dict] = []

    class FakeClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            calls.append(kwargs)
            return _job(filename=kwargs["filename"], dry_run=kwargs.get("dry_run", False))

    at = _run_page(api_client=FakeClient())
    content = _csv_bytes(rows=2)
    at.file_uploader[0].set_value(("employees.csv", content, "text/csv")).run()
    assert calls == []

    at.button[0].click().run()
    assert len(calls) == 1
    assert calls[0]["filename"] == "employees.csv"
    assert calls[0]["content"] == content
    assert calls[0]["dry_run"] is False
    assert any("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in m.value for m in at.markdown)


def test_upload_dry_run_checkbox() -> None:
    calls: list[dict] = []

    class FakeClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            calls.append(kwargs)
            return _job(dry_run=True)

    at = _run_page(api_client=FakeClient())
    at.file_uploader[0].set_value(("batch.csv", _csv_bytes(), "text/csv")).run()
    at.checkbox[0].check().run()
    at.button[0].click().run()
    assert len(calls) == 1
    assert calls[0]["dry_run"] is True
    assert any("yes" in m.value.lower() and "dry" in m.value.lower() for m in at.markdown)


def test_successful_upload_shows_job_id_and_status() -> None:
    class FakeClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            return _job(id="job-123", status="pending", dry_run=False)

    at = _run_page(api_client=FakeClient())
    at.file_uploader[0].set_value(("employees.csv", _csv_bytes(), "text/csv")).run()
    at.button[0].click().run()
    assert not at.exception
    assert any("Upload accepted" in s.value for s in at.success)
    assert any("job-123" in m.value for m in at.markdown)
    assert any("pending" in m.value for m in at.markdown)


def test_api_error_on_upload() -> None:
    class FakeClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            raise ApiError(
                "Only CSV uploads are supported",
                status_code=400,
                code="validation_error",
            )

    at = _run_page(api_client=FakeClient())
    at.file_uploader[0].set_value(("employees.csv", _csv_bytes(), "text/csv")).run()
    at.button[0].click().run()
    assert not at.exception
    assert any("Only CSV uploads are supported" in e.value for e in at.error)


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
    seen: list[bytes] = []

    class FakeClient:
        def upload_import(self, **kwargs):  # noqa: ANN003
            seen.append(kwargs["content"])
            return _job()

    at = _run_page(api_client=FakeClient())
    at.file_uploader[0].set_value(("bom.csv", original, "text/csv")).run()
    at.button[0].click().run()
    assert seen == [original]
