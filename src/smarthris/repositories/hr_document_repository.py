"""Data access for HR document metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smarthris.models.hr_document import HrDocument, HrDocumentStatus, HrDocumentType


class HrDocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, document: HrDocument) -> HrDocument:
        self._session.add(document)
        return document

    def get_by_id(self, document_id: str) -> HrDocument | None:
        return self._session.get(HrDocument, document_id)

    def get_by_id_for_update(self, document_id: str) -> HrDocument | None:
        query = select(HrDocument).where(HrDocument.id == document_id).with_for_update()
        return self._session.scalars(query).one_or_none()

    def get_by_checksum(self, checksum: str) -> HrDocument | None:
        query = select(HrDocument).where(HrDocument.checksum == checksum).limit(1)
        return self._session.scalars(query).first()

    def exists_by_checksum(self, checksum: str) -> bool:
        query = (
            select(func.count())
            .select_from(HrDocument)
            .where(HrDocument.checksum == checksum)
        )
        return int(self._session.scalar(query) or 0) > 0

    def count(
        self,
        *,
        doc_type: HrDocumentType | None = None,
        status: HrDocumentStatus | None = None,
    ) -> int:
        query = select(func.count()).select_from(HrDocument)
        query = self._apply_filters(query, doc_type=doc_type, status=status)
        return int(self._session.scalar(query) or 0)

    def list_page(
        self,
        *,
        offset: int,
        limit: int,
        doc_type: HrDocumentType | None = None,
        status: HrDocumentStatus | None = None,
    ) -> list[HrDocument]:
        query = select(HrDocument)
        query = self._apply_filters(query, doc_type=doc_type, status=status)
        query = query.order_by(HrDocument.uploaded_at.desc(), HrDocument.id.desc())
        query = query.offset(offset).limit(limit)
        return list(self._session.scalars(query).all())

    def mark_processing(self, document: HrDocument) -> HrDocument:
        document.status = HrDocumentStatus.PROCESSING
        document.error_message = None
        self._session.flush()
        return document

    def mark_indexed(self, document: HrDocument) -> HrDocument:
        document.status = HrDocumentStatus.INDEXED
        document.indexed_at = datetime.now(UTC)
        document.error_message = None
        self._session.flush()
        return document

    def mark_failed(self, document: HrDocument, *, error_message: str) -> HrDocument:
        document.status = HrDocumentStatus.FAILED
        document.indexed_at = None
        document.error_message = error_message
        self._session.flush()
        return document

    @staticmethod
    def _apply_filters(
        query,
        *,
        doc_type: HrDocumentType | None,
        status: HrDocumentStatus | None,
    ):
        if doc_type is not None:
            query = query.where(HrDocument.doc_type == doc_type)
        if status is not None:
            query = query.where(HrDocument.status == status)
        return query
