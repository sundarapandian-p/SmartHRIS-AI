"""HR document upload and metadata management."""

from __future__ import annotations

import math
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    StorageError,
    ValidationError,
)
from smarthris.models.hr_document import HrDocument, HrDocumentStatus, HrDocumentType
from smarthris.rag.storage import delete_document_file, save_document_upload
from smarthris.repositories.hr_document_repository import HrDocumentRepository
from smarthris.schemas.hr_document import HrDocumentListResponse, HrDocumentRead


class HrDocumentService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._documents = HrDocumentRepository(session)

    def create_from_upload(
        self,
        *,
        upload: UploadFile,
        title: str | None = None,
        doc_type: HrDocumentType | None = None,
    ) -> HrDocumentRead:
        """
        Store an uploaded PDF/DOCX/TXT privately and persist metadata.

        Duplicate content (same SHA-256) is rejected with ConflictError; the
        newly written file is deleted so no orphan remains.
        """
        cleaned_title = self._normalize_title(title)
        resolved_type = doc_type if doc_type is not None else HrDocumentType.OTHER

        try:
            stored = save_document_upload(
                source=upload.file,
                rag_document_dir=self._settings.rag_document_dir,
                original_filename=upload.filename,
                max_upload_bytes=self._settings.max_upload_bytes,
            )
        except (ValidationError, PayloadTooLargeError, StorageError):
            raise
        except Exception as exc:
            raise StorageError("Failed to store uploaded document") from exc

        storage_path = Path(stored.storage_path)

        try:
            if self._documents.exists_by_checksum(stored.checksum_sha256):
                raise ConflictError("A document with the same content already exists")

            document = HrDocument(
                id=stored.document_id,
                original_filename=stored.original_filename,
                content_type=stored.content_type,
                checksum=stored.checksum_sha256,
                storage_path=str(storage_path),
                title=cleaned_title,
                doc_type=resolved_type,
                status=HrDocumentStatus.PENDING,
            )
            self._documents.add(document)
            self._session.flush()
            self._session.commit()
            self._session.refresh(document)
            return HrDocumentRead.model_validate(document)
        except ConflictError:
            # Duplicate checksum: remove the just-written file; do not overwrite.
            delete_document_file(storage_path)
            raise
        except Exception:
            self._session.rollback()
            delete_document_file(storage_path)
            raise

    def get(self, document_id: str) -> HrDocumentRead:
        document = self._documents.get_by_id(document_id)
        if document is None:
            raise NotFoundError("Document not found")
        return HrDocumentRead.model_validate(document)

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        doc_type: HrDocumentType | None = None,
        status: HrDocumentStatus | None = None,
    ) -> HrDocumentListResponse:
        total = self._documents.count(doc_type=doc_type, status=status)
        offset = (page - 1) * page_size
        rows = self._documents.list_page(
            offset=offset,
            limit=page_size,
            doc_type=doc_type,
            status=status,
        )
        pages = math.ceil(total / page_size) if total else 0
        return HrDocumentListResponse(
            items=[HrDocumentRead.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            pages=pages,
        )

    @staticmethod
    def _normalize_title(title: str | None) -> str | None:
        if title is None:
            return None
        cleaned = str(title).strip()
        if not cleaned:
            return None
        if len(cleaned) > 255:
            raise ValidationError("Document title must be at most 255 characters")
        return cleaned
