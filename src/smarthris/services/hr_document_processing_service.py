"""Background extraction, chunking, embedding, and FAISS indexing for HR documents."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from smarthris.config import Settings
from smarthris.models.hr_document import HrDocument, HrDocumentStatus
from smarthris.models.hr_document_chunk import HrDocumentChunk
from smarthris.rag.chunking import TextChunkDraft, chunk_extracted_document
from smarthris.rag.embeddings import (
    DocumentProcessingError,
    EmbeddingProvider,
    encode_texts,
    embedding_dimension,
)
from smarthris.rag.extraction import DocumentExtractionError, extract_document_text
from smarthris.rag.bm25_store import rebuild_persisted_bm25_from_records
from smarthris.rag.faiss_store import (
    ChunkVectorRecord,
    publish_index_after_mysql_commit,
    rebuild_persisted_index_from_records,
)
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.repositories.hr_document_repository import HrDocumentRepository

logger = logging.getLogger(__name__)

_MAX_ERROR_MESSAGE_LEN = 1000
_EMBED_BATCH_SIZE = 32

# Process-local: serializes chunk replacement, corpus snapshot, MySQL index commit,
# FAISS publish, and BM25 publish for all documents in this Python process.
# Does not coordinate across multiple Uvicorn workers or OS processes.
_document_indexing_pipeline_lock = threading.Lock()

"""
Recovery for documents stuck in ``processing`` (e.g. after a process crash):

- ``POST /documents/{id}/reindex`` returns **409** while status is ``processing``.
- An operator with database access may set ``status`` to ``failed`` (and clear
  ``error_message`` if desired), then call reindex.
- To realign FAISS with MySQL after any divergence, call
  ``reconcile_vector_index_from_database`` (or reindex all ``indexed`` docs).
"""


def _sanitize_error_message(message: str) -> str:
    text = " ".join(str(message).split())
    if len(text) > _MAX_ERROR_MESSAGE_LEN:
        text = text[: _MAX_ERROR_MESSAGE_LEN - 3] + "..."
    return text


def reconcile_vector_index_from_database(
    session: Session,
    settings: Settings,
    *,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """Rebuild live FAISS and BM25 indexes from all ``indexed`` document chunks in MySQL."""
    with _document_indexing_pipeline_lock:
        chunks_repo = HrDocumentChunkRepository(session)
        rows = chunks_repo.list_for_index_rebuild()
        records = [ChunkVectorRecord(chunk_id=row.id, chunk_text=row.chunk_text) for row in rows]
        dimension = embedding_dimension(settings, provider=embedding_provider)

        def embed_batch(texts: list[str]):
            return encode_texts(
                texts,
                settings=settings,
                provider=embedding_provider,
                batch_size=_EMBED_BATCH_SIZE,
            )

        rebuild_persisted_index_from_records(
            records,
            settings=settings,
            dimension=dimension,
            embed_fn=embed_batch,
            batch_size=_EMBED_BATCH_SIZE,
        )
        try:
            rebuild_persisted_bm25_from_records(records, settings=settings)
        except Exception:
            logger.exception("Failed to rebuild BM25 index during database reconciliation")


class HrDocumentProcessingService:
    def __init__(
        self,
        session_factory: Callable[[], Session] | sessionmaker[Session],
        settings: Settings,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._embedding_provider = embedding_provider

    def process(self, document_id: str) -> None:
        session = self._session_factory()
        try:
            self._process_with_session(session, document_id)
        except Exception as exc:
            logger.exception("Unhandled HR document processing error for %s", document_id)
            self._mark_failed_fresh(
                document_id,
                "Document indexing failed",
                original_exc=exc,
            )
            raise
        finally:
            session.close()

    def _process_with_session(self, session: Session, document_id: str) -> None:
        documents = HrDocumentRepository(session)
        document = documents.get_by_id_for_update(document_id)
        if document is None:
            logger.info("HR document %s not found; skipping", document_id)
            return

        if document.status == HrDocumentStatus.PROCESSING:
            logger.info(
                "HR document %s is already processing; skipping duplicate run",
                document_id,
            )
            return

        if document.status not in {
            HrDocumentStatus.PENDING,
            HrDocumentStatus.FAILED,
            HrDocumentStatus.INDEXED,
        }:
            logger.info(
                "HR document %s status is %s; skipping",
                document_id,
                document.status.value,
            )
            return

        try:
            documents.mark_processing(document)
            session.commit()
        except Exception:
            logger.exception("Failed to mark HR document %s as processing", document_id)
            session.rollback()
            raise

        document = documents.get_by_id(document_id)
        if document is None:
            logger.error("HR document %s disappeared after mark_processing", document_id)
            self._mark_failed_fresh(document_id, "Document metadata missing during processing")
            return

        try:
            extracted = extract_document_text(document.storage_path)
            drafts = chunk_extracted_document(
                extracted,
                chunk_size=self._settings.rag_chunk_size,
                chunk_overlap=self._settings.rag_chunk_overlap,
            )
        except DocumentExtractionError as exc:
            self._mark_failed(session, document_id, _sanitize_error_message(exc.message))
            return
        except ValueError as exc:
            self._mark_failed(session, document_id, _sanitize_error_message(str(exc)))
            return
        except Exception as exc:
            logger.exception("Unexpected extraction failure for HR document %s", document_id)
            self._mark_failed_fresh(
                document_id,
                "Document text extraction failed",
                original_exc=exc,
            )
            return

        try:
            self._replace_chunks_and_index(session, document, drafts)
        except DocumentProcessingError as exc:
            session.rollback()
            self._mark_failed_fresh(
                document_id,
                _sanitize_error_message(exc.message),
                original_exc=exc,
            )
        except Exception as exc:
            logger.exception("Unexpected indexing failure for HR document %s", document_id)
            session.rollback()
            self._mark_failed_fresh(
                document_id,
                "Document indexing failed",
                original_exc=exc,
            )

    def _replace_chunks_and_index(
        self,
        session: Session,
        document: HrDocument,
        drafts: list[TextChunkDraft],
    ) -> None:
        with _document_indexing_pipeline_lock:
            self._replace_chunks_and_index_locked(session, document, drafts)

    def _replace_chunks_and_index_locked(
        self,
        session: Session,
        document: HrDocument,
        drafts: list[TextChunkDraft],
    ) -> None:
        documents = HrDocumentRepository(session)
        chunks_repo = HrDocumentChunkRepository(session)
        document_id = document.id

        chunks_repo.delete_by_document_id(document_id)
        orm_chunks = [
            HrDocumentChunk(
                document_id=document_id,
                chunk_index=draft.chunk_index,
                chunk_text=draft.chunk_text,
                page_number=draft.page_number,
                section_title=draft.section_title,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
                token_count=draft.token_count,
            )
            for draft in drafts
        ]
        chunks_repo.add_many(orm_chunks)
        session.flush()

        dimension = embedding_dimension(
            self._settings,
            provider=self._embedding_provider,
        )

        def embed_batch(texts: list[str]):
            return encode_texts(
                texts,
                settings=self._settings,
                provider=self._embedding_provider,
                batch_size=_EMBED_BATCH_SIZE,
            )

        rebuild_rows = chunks_repo.list_for_index_rebuild(
            include_document_ids={document_id},
        )
        records = [
            ChunkVectorRecord(chunk_id=row.id, chunk_text=row.chunk_text) for row in rebuild_rows
        ]

        def commit_indexed() -> None:
            current = documents.get_by_id(document_id)
            if current is None:
                raise DocumentProcessingError("Document disappeared during indexing")
            documents.mark_indexed(current)
            session.commit()

        publish_index_after_mysql_commit(
            records,
            settings=self._settings,
            dimension=dimension,
            embed_fn=embed_batch,
            batch_size=_EMBED_BATCH_SIZE,
            mysql_commit=commit_indexed,
        )
        try:
            rebuild_persisted_bm25_from_records(records, settings=self._settings)
        except Exception:
            logger.exception(
                "Failed to rebuild BM25 index after successful document indexing",
            )

    def _mark_failed(self, session: Session, document_id: str, message: str) -> None:
        try:
            documents = HrDocumentRepository(session)
            document = documents.get_by_id_for_update(document_id)
            if document is None:
                return
            if document.status not in {
                HrDocumentStatus.PROCESSING,
                HrDocumentStatus.INDEXED,
            }:
                return
            documents.mark_failed(document, error_message=message)
            session.commit()
        except Exception as mark_exc:
            logger.exception("Unable to mark HR document %s as failed", document_id)
            try:
                session.rollback()
            except Exception:
                pass
            self._mark_failed_fresh(document_id, message, original_exc=mark_exc)

    def _mark_failed_fresh(
        self,
        document_id: str,
        message: str,
        *,
        original_exc: BaseException | None = None,
    ) -> None:
        session = self._session_factory()
        try:
            documents = HrDocumentRepository(session)
            document = documents.get_by_id_for_update(document_id)
            if document is None:
                return
            if document.status not in {
                HrDocumentStatus.PROCESSING,
                HrDocumentStatus.INDEXED,
            }:
                return
            documents.mark_failed(document, error_message=_sanitize_error_message(message))
            session.commit()
        except Exception as status_exc:
            if original_exc is not None:
                logger.error(
                    "HR document %s failed during processing before status could be updated",
                    document_id,
                    exc_info=original_exc,
                )
            logger.error(
                "Unable to mark HR document %s as failed using a fresh session",
                document_id,
                exc_info=status_exc,
            )
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()


def process_hr_document(
    document_id: str,
    *,
    session_factory: Callable[[], Session] | sessionmaker[Session],
    settings: Settings,
) -> None:
    """BackgroundTasks entrypoint (mirror ``process_import_job``)."""
    try:
        HrDocumentProcessingService(session_factory, settings).process(document_id)
    except Exception:
        logger.exception("Background HR document processing failed for %s", document_id)


def request_document_reindex(session: Session, document_id: str) -> HrDocument:
    """
    Validate and transition a document for manual reindex.

    Raises ``ConflictError`` when already processing; ``NotFoundError`` when missing.
    """
    from smarthris.core.exceptions import ConflictError, NotFoundError

    documents = HrDocumentRepository(session)
    document = documents.get_by_id(document_id)
    if document is None:
        raise NotFoundError("Document not found")
    if document.status == HrDocumentStatus.PROCESSING:
        raise ConflictError("Document is already processing")
    if document.status not in {
        HrDocumentStatus.PENDING,
        HrDocumentStatus.FAILED,
        HrDocumentStatus.INDEXED,
    }:
        raise ConflictError("Document cannot be reindexed in its current state")

    return document
