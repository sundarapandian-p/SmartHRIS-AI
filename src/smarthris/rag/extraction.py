"""Text extraction from private HR document files (PDF, DOCX, TXT)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from smarthris.rag.storage import ALLOWED_EXTENSIONS


class DocumentExtractionError(Exception):
    """Non-retryable extraction failure with a sanitized client-safe message."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ExtractedSpan:
    """A contiguous text span with source metadata and document offsets."""

    text: str
    start_offset: int
    end_offset: int
    page_number: int | None
    section_title: str | None


@dataclass(frozen=True)
class ExtractedDocument:
    """Full extracted text assembled from ordered spans."""

    full_text: str
    spans: tuple[ExtractedSpan, ...]


_HEADING_STYLE_RE = re.compile(r"^Heading\s*(\d+)\s*$", re.IGNORECASE)


def extract_document_text(storage_path: str | Path) -> ExtractedDocument:
    """
    Extract text from a server-controlled storage path.

    Never call with user-supplied paths; use ``HrDocument.storage_path`` only.
    """
    path = Path(storage_path)
    if not path.is_file():
        raise DocumentExtractionError("Document file is missing or unreadable")

    suffix = path.suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise DocumentExtractionError("Unsupported document type for text extraction")

    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    return _extract_txt(path)


def metadata_at_offset(spans: tuple[ExtractedSpan, ...], offset: int) -> tuple[int | None, str | None]:
    """Return (page_number, section_title) for a character offset into ``full_text``."""
    if offset < 0:
        return None, None
    page_number: int | None = None
    section_title: str | None = None
    for span in spans:
        if offset >= span.start_offset and offset < span.end_offset:
            return span.page_number, span.section_title
        if offset >= span.start_offset:
            page_number = span.page_number
            section_title = span.section_title
    return page_number, section_title


def _build_document(parts: list[tuple[str, int | None, str | None]]) -> ExtractedDocument:
    spans: list[ExtractedSpan] = []
    pieces: list[str] = []
    cursor = 0
    for text, page_number, section_title in parts:
        cleaned = text.strip()
        if not cleaned:
            continue
        if pieces:
            pieces.append("\n\n")
            cursor += 2
        start = cursor
        pieces.append(cleaned)
        cursor += len(cleaned)
        spans.append(
            ExtractedSpan(
                text=cleaned,
                start_offset=start,
                end_offset=cursor,
                page_number=page_number,
                section_title=section_title,
            )
        )
    full_text = "".join(pieces)
    if not full_text.strip():
        raise DocumentExtractionError("No extractable text found in document")
    return ExtractedDocument(full_text=full_text, spans=tuple(spans))


def _extract_txt(path: Path) -> ExtractedDocument:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DocumentExtractionError("Document file could not be read") from exc
    if b"\x00" in raw:
        raise DocumentExtractionError("Document contains binary content")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise DocumentExtractionError("No extractable text found in document")
    return _build_document([(text, None, None)])


def _extract_pdf(path: Path) -> ExtractedDocument:
    try:
        reader = PdfReader(str(path))
    except PdfReadError as exc:
        raise DocumentExtractionError("Document PDF is corrupt or unreadable") from exc
    except OSError as exc:
        raise DocumentExtractionError("Document file could not be read") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if unlocked == 0:
            raise DocumentExtractionError("Password-protected PDFs are not supported")

    parts: list[tuple[str, int | None, str | None]] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise DocumentExtractionError(
                f"Failed to extract text from PDF page {page_index}"
            ) from exc
        if page_text.strip():
            parts.append((page_text, page_index, None))

    if not parts:
        raise DocumentExtractionError("No extractable text found in document")
    return _build_document(parts)


def _extract_docx(path: Path) -> ExtractedDocument:
    try:
        document = DocxDocument(str(path))
    except OSError as exc:
        raise DocumentExtractionError("Document file could not be read") from exc
    except Exception as exc:
        raise DocumentExtractionError("Document DOCX is corrupt or unreadable") from exc

    current_section: str | None = None
    parts: list[tuple[str, int | None, str | None]] = []
    for paragraph in document.paragraphs:
        text = paragraph.text or ""
        style_name = getattr(getattr(paragraph, "style", None), "name", "") or ""
        if _HEADING_STYLE_RE.match(style_name.strip()) and text.strip():
            heading = text.strip()
            current_section = heading[:255]
            continue
        if text.strip():
            parts.append((text, None, current_section))

    if not parts:
        raise DocumentExtractionError("No extractable text found in document")
    return _build_document(parts)
