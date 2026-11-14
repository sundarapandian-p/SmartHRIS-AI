"""Semantic retrieval over indexed HR document chunks."""

from __future__ import annotations

from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import ValidationError
from smarthris.rag.embeddings import (
    DocumentProcessingError,
    EmbeddingProvider,
    encode_texts,
    embedding_dimension,
)
from smarthris.rag.faiss_search import search_chunks_by_vector
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.schemas.hr_document import (
    HrDocumentSearchRequest,
    HrDocumentSearchResponse,
    HrDocumentSearchResultItem,
)

_MAX_QUERY_LENGTH = 2000
_MAX_TOP_K = 20


class HrDocumentRetrievalService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._embedding_provider = embedding_provider

    def search(self, payload: HrDocumentSearchRequest) -> HrDocumentSearchResponse:
        query = payload.query.strip()
        if not query:
            raise ValidationError("Search query must not be empty")
        if len(query) > _MAX_QUERY_LENGTH:
            raise ValidationError(
                f"Search query must be at most {_MAX_QUERY_LENGTH} characters",
            )

        top_k = payload.top_k if payload.top_k is not None else self._settings.rag_top_k
        if top_k < 1 or top_k > _MAX_TOP_K:
            raise ValidationError(f"top_k must be between 1 and {_MAX_TOP_K}")

        try:
            dimension = embedding_dimension(
                self._settings,
                provider=self._embedding_provider,
            )
            vectors = encode_texts(
                [query],
                settings=self._settings,
                provider=self._embedding_provider,
            )
            query_vector = vectors[0]
            index_ready, hits = search_chunks_by_vector(
                query_vector,
                settings=self._settings,
                dimension=dimension,
                top_k=top_k,
            )
        except DocumentProcessingError as exc:
            raise ValidationError("Document search is temporarily unavailable") from exc

        if not index_ready:
            return HrDocumentSearchResponse(
                query=query,
                index_ready=False,
                items=[],
            )

        if not hits:
            return HrDocumentSearchResponse(
                query=query,
                index_ready=True,
                items=[],
            )

        chunk_repo = HrDocumentChunkRepository(self._session)
        mapped = chunk_repo.map_indexed_chunks_by_ids(
            [hit.chunk_id for hit in hits],
            doc_type=payload.doc_type,
        )

        items: list[HrDocumentSearchResultItem] = []
        rank = 0
        for hit in hits:
            row = mapped.get(hit.chunk_id)
            if row is None:
                continue
            chunk, document = row
            rank += 1
            title = document.title
            if not title or not str(title).strip():
                title = document.original_filename
            items.append(
                HrDocumentSearchResultItem(
                    rank=rank,
                    score=float(hit.score),
                    chunk_id=chunk.id,
                    document_id=document.id,
                    document_title=title,
                    original_filename=document.original_filename,
                    doc_type=document.doc_type,
                    chunk_index=chunk.chunk_index,
                    chunk_text=chunk.chunk_text,
                    page_number=chunk.page_number,
                    section_title=chunk.section_title,
                )
            )

        return HrDocumentSearchResponse(
            query=query,
            index_ready=True,
            items=items,
        )