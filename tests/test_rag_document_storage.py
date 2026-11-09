"""Focused tests for private HR document storage (Phase 5.3.1)."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from uuid import UUID

import pytest

from smarthris.core.exceptions import PayloadTooLargeError, StorageError, ValidationError
from smarthris.rag.storage import (
    CHUNK_SIZE,
    DEFAULT_MAX_UPLOAD_BYTES,
    DocumentStorageResult,
    assert_allowed_document_extension,
    delete_document_file,
    document_storage_path,
    ensure_document_dir,
    save_document_upload,
    sanitize_original_filename,
)


def _minimal_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _minimal_docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        archive.writestr("word/document.xml", "<document></document>")
    return buffer.getvalue()


def _invalid_docx_zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("readme.txt", "not a docx")
    return buffer.getvalue()


def test_sanitize_strips_path_traversal() -> None:
    assert sanitize_original_filename(r"..\..\Windows\policy.pdf") == "policy.pdf"
    assert sanitize_original_filename("/tmp/nested/handbook.DOCX") == "handbook.DOCX"
    with pytest.raises(ValidationError):
        sanitize_original_filename("")
    with pytest.raises(ValidationError):
        sanitize_original_filename(None)


def test_unsupported_extensions_rejected() -> None:
    with pytest.raises(ValidationError, match="Unsupported document type"):
        assert_allowed_document_extension("employees.csv")
    with pytest.raises(ValidationError, match="Unsupported document type"):
        assert_allowed_document_extension("photo.png")


def test_uuid_based_storage_path_and_containment(tmp_path: Path) -> None:
    base = ensure_document_dir(tmp_path / "documents")
    doc_id = "12345678-1234-4234-8234-123456789abc"
    destination = document_storage_path(base, doc_id, ".pdf")
    assert destination.parent == base
    assert destination.name == f"{doc_id}.pdf"
    UUID(doc_id)
    assert destination.resolve().is_relative_to(base.resolve())


def test_path_containment_rejects_escape(tmp_path: Path) -> None:
    base = ensure_document_dir(tmp_path / "documents")
    with pytest.raises(ValidationError, match="Invalid document id"):
        document_storage_path(base, "../escape", ".pdf")


def test_valid_pdf_signature(tmp_path: Path) -> None:
    payload = _minimal_pdf_bytes()
    result = save_document_upload(
        source=io.BytesIO(payload),
        rag_document_dir=tmp_path / "documents",
        original_filename="leave-policy.pdf",
        document_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    )
    assert isinstance(result, DocumentStorageResult)
    assert result.original_filename == "leave-policy.pdf"
    assert result.content_type == "application/pdf"
    assert result.size_bytes == len(payload)
    assert result.storage_path.read_bytes().startswith(b"%PDF")
    assert result.storage_path.name.endswith(".pdf")


def test_valid_docx_zip_structure(tmp_path: Path) -> None:
    payload = _minimal_docx_bytes()
    result = save_document_upload(
        source=io.BytesIO(payload),
        rag_document_dir=tmp_path / "documents",
        original_filename="handbook.docx",
    )
    assert result.content_type.endswith("wordprocessingml.document")
    assert result.storage_path.is_file()
    with zipfile.ZipFile(result.storage_path, "r") as archive:
        names = set(archive.namelist())
    assert "[Content_Types].xml" in names
    assert "word/document.xml" in names


def test_invalid_docx_zip_rejected_and_cleaned(tmp_path: Path) -> None:
    destination_dir = tmp_path / "documents"
    with pytest.raises(ValidationError, match="missing required"):
        save_document_upload(
            source=io.BytesIO(_invalid_docx_zip_bytes()),
            rag_document_dir=destination_dir,
            original_filename="fake.docx",
            document_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
    leftover = destination_dir / "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb.docx"
    assert not leftover.exists()


def test_valid_txt(tmp_path: Path) -> None:
    payload = "Employee handbook excerpt.\nLeave policy section.\n".encode("utf-8")
    result = save_document_upload(
        source=io.BytesIO(payload),
        rag_document_dir=tmp_path / "documents",
        original_filename="notes.txt",
    )
    assert result.content_type == "text/plain"
    assert result.storage_path.read_bytes() == payload


def test_binary_txt_rejected(tmp_path: Path) -> None:
    destination_dir = tmp_path / "documents"
    with pytest.raises(ValidationError, match="binary"):
        save_document_upload(
            source=io.BytesIO(b"hello\x00world"),
            rag_document_dir=destination_dir,
            original_filename="bad.txt",
            document_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        )
    assert not (destination_dir / "cccccccc-cccc-4ccc-8ccc-cccccccccccc.txt").exists()


def test_empty_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="empty"):
        save_document_upload(
            source=io.BytesIO(b""),
            rag_document_dir=tmp_path / "documents",
            original_filename="empty.pdf",
        )


def test_oversized_file_rejected_and_cleaned(tmp_path: Path) -> None:
    destination_dir = tmp_path / "documents"
    doc_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    with pytest.raises(PayloadTooLargeError):
        save_document_upload(
            source=io.BytesIO(b"%PDF-" + b"x" * 100),
            rag_document_dir=destination_dir,
            original_filename="big.pdf",
            document_id=doc_id,
            max_upload_bytes=20,
            chunk_size=8,
        )
    assert not (destination_dir / f"{doc_id}.pdf").exists()


def test_checksum_correctness(tmp_path: Path) -> None:
    payload = _minimal_pdf_bytes()
    result = save_document_upload(
        source=io.BytesIO(payload),
        rag_document_dir=tmp_path / "documents",
        original_filename="checksum.pdf",
    )
    assert result.checksum_sha256 == hashlib.sha256(payload).hexdigest()


def test_exclusive_create_no_overwrite(tmp_path: Path) -> None:
    destination_dir = tmp_path / "documents"
    doc_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    first = save_document_upload(
        source=io.BytesIO(_minimal_pdf_bytes()),
        rag_document_dir=destination_dir,
        original_filename="one.pdf",
        document_id=doc_id,
    )
    with pytest.raises(StorageError, match="already exists"):
        save_document_upload(
            source=io.BytesIO(_minimal_pdf_bytes() + b"\n"),
            rag_document_dir=destination_dir,
            original_filename="two.pdf",
            document_id=doc_id,
        )
    assert first.storage_path.read_bytes() == _minimal_pdf_bytes()


def test_pdf_extension_with_non_pdf_content_cleaned(tmp_path: Path) -> None:
    destination_dir = tmp_path / "documents"
    doc_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    with pytest.raises(ValidationError, match="not a valid PDF"):
        save_document_upload(
            source=io.BytesIO(b"not-a-pdf"),
            rag_document_dir=destination_dir,
            original_filename="lie.pdf",
            document_id=doc_id,
        )
    assert not (destination_dir / f"{doc_id}.pdf").exists()


def test_stream_uses_bounded_chunks(tmp_path: Path) -> None:
    class ChunkProbe(io.BytesIO):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            self.max_requested = 0

        def read(self, size: int | None = -1) -> bytes:
            if size is None or size < 0:
                raise AssertionError("unbounded read is not allowed")
            self.max_requested = max(self.max_requested, size)
            return super().read(size)

    payload = b"%PDF-" + b"y" * 10_000
    probe = ChunkProbe(payload)
    save_document_upload(
        source=probe,
        rag_document_dir=tmp_path / "documents",
        original_filename="probe.pdf",
        chunk_size=CHUNK_SIZE,
    )
    assert probe.max_requested == CHUNK_SIZE


def test_default_max_upload_matches_fifty_mib() -> None:
    assert DEFAULT_MAX_UPLOAD_BYTES == 52_428_800


def test_delete_document_file_best_effort(tmp_path: Path) -> None:
    path = tmp_path / "gone.pdf"
    path.write_bytes(_minimal_pdf_bytes())
    delete_document_file(path)
    assert not path.exists()
    delete_document_file(path)  # missing_ok
