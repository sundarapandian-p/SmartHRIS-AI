"""Unit tests for HR document text extraction."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from smarthris.rag.extraction import DocumentExtractionError, extract_document_text


def _write_pdf(path: Path, *, pages: list[str]) -> None:
    writer = PdfWriter()
    for _ in pages:
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)
    # Re-open with pypdf to inject text is heavy; use simple text files for TXT instead.
    # For multi-page signal, use two minimal files or docx.


def _minimal_pdf_with_text(path: Path, text: str) -> None:
    # Minimal valid PDF with stream content (extract_text may still be sparse).
    payload = (
        f"%PDF-1.4\n1 0 obj<<>>endobj\n"
        f"2 0 obj<</Length {len(text)}>>\nstream\n{text}\nendstream\nendobj\n"
        f"trailer<<>>\n%%EOF\n"
    )
    path.write_bytes(payload.encode("ascii"))


def _docx_bytes(*, paragraphs: list[tuple[str, str | None]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types></Types>")
        body_parts: list[str] = []
        for text, style in paragraphs:
            if style:
                body_parts.append(
                    f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
                    f"<w:r><w:t>{text}</w:t></w:r></w:p>"
                )
            else:
                body_parts.append(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>")
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{''.join(body_parts)}</w:body></w:document>",
        )
    return buffer.getvalue()


def test_txt_extraction(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("Leave policy applies to all staff.\n", encoding="utf-8")
    doc = extract_document_text(path)
    assert "Leave policy" in doc.full_text
    assert doc.spans[0].start_offset == 0


def test_docx_extraction_and_headings(tmp_path: Path) -> None:
    from docx import Document as DocxDocument

    path = tmp_path / "handbook.docx"
    document = DocxDocument()
    document.add_paragraph("Benefits", style="Heading 1")
    document.add_paragraph("Dental coverage is included.")
    document.save(path)
    doc = extract_document_text(path)
    assert "Dental coverage" in doc.full_text
    assert any(span.section_title == "Benefits" for span in doc.spans)


def test_pdf_extraction_rejects_empty(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    path = tmp_path / "empty.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(DocumentExtractionError, match="No extractable text"):
        extract_document_text(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DocumentExtractionError):
        extract_document_text(tmp_path / "missing.pdf")
