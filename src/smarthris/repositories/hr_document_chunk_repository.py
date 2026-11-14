"""Data access for HR document chunks."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from smarthris.models.hr_document import (
    HrDocument,
    HrDocumentStatus,
    HrDocumentType,
    HrDocumentVisibility,
)
from smarthris.models.hr_document_chunk import HrDocumentChunk


class HrDocumentChunkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def delete_by_document_id(self, document_id: str) -> int:
        result = self._session.execute(
            delete(HrDocumentChunk).where(HrDocumentChunk.document_id == document_id)
        )
        return int(result.rowcount or 0)

    def add_many(self, chunks: list[HrDocumentChunk]) -> None:
        if not chunks:
            return
        self._session.add_all(chunks)

    def list_by_document_id(self, document_id: str) -> list[HrDocumentChunk]:
        query = (
            select(HrDocumentChunk)
            .where(HrDocumentChunk.document_id == document_id)
            .order_by(HrDocumentChunk.chunk_index.asc())
        )
        return list(self._session.scalars(query).all())

    def list_for_index_rebuild(
        self,
        *,
        include_document_ids: set[str] | frozenset[str] = frozenset(),
    ) -> list[HrDocumentChunk]:
        """
        Return chunks eligible for the global FAISS index.

        Includes chunks from ``INDEXED`` documents plus any explicit ids
        (e.g. the document currently being indexed).
        """
        predicates = [HrDocument.status == HrDocumentStatus.INDEXED]
        if include_document_ids:
            predicates.append(HrDocument.id.in_(tuple(include_document_ids)))
        query = (
            select(HrDocumentChunk)
            .join(HrDocument, HrDocumentChunk.document_id == HrDocument.id)
            .where(or_(*predicates))
            .order_by(HrDocumentChunk.id.asc())
        )
        return list(self._session.scalars(query).all())

    def indexed_chunk_id_allowlist(
        self,
        *,
        visibilities: frozenset[HrDocumentVisibility],
    ) -> frozenset[int]:
        """Chunk ids eligible for retrieval for the given visibility set."""
        if not visibilities:
            return frozenset()
        query = (
            select(HrDocumentChunk.id)
            .join(HrDocument, HrDocumentChunk.document_id == HrDocument.id)
            .where(HrDocument.status == HrDocumentStatus.INDEXED)
            .where(HrDocument.visibility.in_(tuple(visibilities)))
        )
        rows = self._session.scalars(query).all()
        return frozenset(int(chunk_id) for chunk_id in rows)

    def map_indexed_chunks_by_ids(
        self,
        chunk_ids: Sequence[int],
        *,
        doc_type: HrDocumentType | None = None,
        visibilities: frozenset[HrDocumentVisibility] | None = None,
    ) -> dict[int, tuple[HrDocumentChunk, HrDocument]]:
        """
        Return chunks joined with parent documents for retrieval.

        Only ``INDEXED`` parents are included. Missing or stale ids are omitted.
        """
        unique_ids = [int(cid) for cid in dict.fromkeys(chunk_ids)]
        if not unique_ids:
            return {}

        query = (
            select(HrDocumentChunk, HrDocument)
            .join(HrDocument, HrDocumentChunk.document_id == HrDocument.id)
            .where(HrDocumentChunk.id.in_(unique_ids))
            .where(HrDocument.status == HrDocumentStatus.INDEXED)
        )
        if doc_type is not None:
            query = query.where(HrDocument.doc_type == doc_type)
        if visibilities is not None:
            query = query.where(HrDocument.visibility.in_(tuple(visibilities)))

        rows = self._session.execute(query).all()
        return {int(chunk.id): (chunk, document) for chunk, document in rows}
