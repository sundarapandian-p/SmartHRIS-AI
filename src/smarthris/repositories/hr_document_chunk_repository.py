"""Data access for HR document chunks."""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from smarthris.models.hr_document import HrDocument, HrDocumentStatus
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
