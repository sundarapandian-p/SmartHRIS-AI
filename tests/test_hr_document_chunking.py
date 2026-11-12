"""Unit tests for HR document chunking."""

from __future__ import annotations

from smarthris.rag.chunking import chunk_extracted_document
from smarthris.rag.extraction import ExtractedDocument, ExtractedSpan


def test_chunk_overlap_and_offsets() -> None:
    text = "a" * 1000
    document = ExtractedDocument(
        full_text=text,
        spans=(
            ExtractedSpan(
                text=text,
                start_offset=0,
                end_offset=1000,
                page_number=1,
                section_title="Policy",
            ),
        ),
    )
    chunks = chunk_extracted_document(document, chunk_size=800, chunk_overlap=120)
    assert chunks[0].chunk_index == 0
    assert chunks[0].start_offset == 0
    assert chunks[1].chunk_index == 1
    assert chunks[1].start_offset == 800 - 120
    assert chunks[0].page_number == 1
    assert chunks[0].section_title == "Policy"
    assert chunks[0].token_count >= 1
