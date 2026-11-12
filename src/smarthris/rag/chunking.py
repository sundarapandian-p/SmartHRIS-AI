"""Character-based chunking for extracted HR documents."""

from __future__ import annotations

from dataclasses import dataclass

from smarthris.rag.extraction import ExtractedDocument, metadata_at_offset


@dataclass(frozen=True)
class TextChunkDraft:
    """In-memory chunk prior to persistence."""

    chunk_index: int
    chunk_text: str
    page_number: int | None
    section_title: str | None
    start_offset: int
    end_offset: int
    token_count: int


def chunk_extracted_document(
    document: ExtractedDocument,
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[TextChunkDraft]:
    """
    Split ``document.full_text`` into overlapping character windows.

    Skips empty slices. ``chunk_index`` starts at zero per document.
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    if chunk_overlap < 0:
        raise ValueError("chunk_overlap must be non-negative")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    text = document.full_text
    if not text.strip():
        return []

    step = chunk_size - chunk_overlap
    drafts: list[TextChunkDraft] = []
    start = 0
    chunk_index = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)
        slice_text = text[start:end].strip()
        if slice_text:
            page_number, section_title = metadata_at_offset(document.spans, start)
            if section_title and len(section_title) > 255:
                section_title = section_title[:255]
            drafts.append(
                TextChunkDraft(
                    chunk_index=chunk_index,
                    chunk_text=slice_text,
                    page_number=page_number,
                    section_title=section_title,
                    start_offset=start,
                    end_offset=end,
                    token_count=len(slice_text.split()),
                )
            )
            chunk_index += 1
        if end >= length:
            break
        start += step

    if not drafts:
        raise ValueError("Chunking produced no non-empty chunks")
    return drafts
