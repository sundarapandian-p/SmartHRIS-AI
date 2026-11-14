"""Retrieval over indexed HR document chunks (semantic, keyword, hybrid)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.core.exceptions import ValidationError
from smarthris.rag.bm25_store import search_chunks_by_query
from smarthris.rag.embeddings import (
    DocumentProcessingError,
    EmbeddingProvider,
    encode_texts,
    embedding_dimension,
)
from smarthris.rag.faiss_search import search_chunks_by_vector
from smarthris.rag.rrf import reciprocal_rank_fusion
from smarthris.repositories.hr_document_chunk_repository import HrDocumentChunkRepository
from smarthris.schemas.hr_document import (
    HrDocumentRetrievalMode,
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

        candidates = self._settings.rag_retrieval_candidates
        if candidates < top_k:
            raise ValidationError(
                "rag_retrieval_candidates must be at least as large as top_k",
            )

        mode = payload.retrieval_mode

        if mode == HrDocumentRetrievalMode.SEMANTIC:
            return self._search_semantic(query, top_k=top_k, doc_type=payload.doc_type)

        if mode == HrDocumentRetrievalMode.KEYWORD:
            return self._search_keyword(query, top_k=top_k, doc_type=payload.doc_type)

        return self._search_hybrid(
            query,
            top_k=top_k,
            candidates=candidates,
            doc_type=payload.doc_type,
        )

    def _search_semantic(
        self,
        query: str,
        *,
        top_k: int,
        doc_type,
    ) -> HrDocumentSearchResponse:
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
            index_ready, hits = search_chunks_by_vector(
                vectors[0],
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
                retrieval_mode=HrDocumentRetrievalMode.SEMANTIC,
                items=[],
            )

        scored = [(hit.chunk_id, hit.score) for hit in hits]
        items = self._hydrate_items(scored, doc_type=doc_type)
        return HrDocumentSearchResponse(
            query=query,
            index_ready=True,
            retrieval_mode=HrDocumentRetrievalMode.SEMANTIC,
            items=items,
        )

    def _search_keyword(
        self,
        query: str,
        *,
        top_k: int,
        doc_type,
    ) -> HrDocumentSearchResponse:
        try:
            index_ready, hits = search_chunks_by_query(
                query,
                settings=self._settings,
                top_k=top_k,
            )
        except DocumentProcessingError as exc:
            raise ValidationError("Document search is temporarily unavailable") from exc

        if not index_ready:
            return HrDocumentSearchResponse(
                query=query,
                index_ready=False,
                retrieval_mode=HrDocumentRetrievalMode.KEYWORD,
                items=[],
            )

        scored = [(hit.chunk_id, hit.score) for hit in hits]
        items = self._hydrate_items(scored, doc_type=doc_type)
        return HrDocumentSearchResponse(
            query=query,
            index_ready=True,
            retrieval_mode=HrDocumentRetrievalMode.KEYWORD,
            items=items,
        )

    def _search_hybrid(
        self,
        query: str,
        *,
        top_k: int,
        candidates: int,
        doc_type,
    ) -> HrDocumentSearchResponse:
        semantic_ids: list[int] = []
        keyword_ids: list[int] = []
        semantic_ready = False
        keyword_ready = False

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
            semantic_ready, sem_hits = search_chunks_by_vector(
                vectors[0],
                settings=self._settings,
                dimension=dimension,
                top_k=candidates,
            )
            semantic_ids = [hit.chunk_id for hit in sem_hits]
        except DocumentProcessingError:
            semantic_ready = False

        try:
            keyword_ready, kw_hits = search_chunks_by_query(
                query,
                settings=self._settings,
                top_k=candidates,
            )
            keyword_ids = [hit.chunk_id for hit in kw_hits]
        except DocumentProcessingError:
            keyword_ready = False

        if not semantic_ready and not keyword_ready:
            return HrDocumentSearchResponse(
                query=query,
                index_ready=False,
                retrieval_mode=HrDocumentRetrievalMode.HYBRID,
                items=[],
            )

        fused = reciprocal_rank_fusion(
            semantic_ids,
            keyword_ids,
            k=self._settings.rag_rrf_constant,
        )[:top_k]
        scored = list(fused)
        items = self._hydrate_items(scored, doc_type=doc_type)
        return HrDocumentSearchResponse(
            query=query,
            index_ready=True,
            retrieval_mode=HrDocumentRetrievalMode.HYBRID,
            items=items,
        )

    def _hydrate_items(
        self,
        ranked_scores: list[tuple[int, float]],
        *,
        doc_type,
    ) -> list[HrDocumentSearchResultItem]:
        if not ranked_scores:
            return []

        chunk_repo = HrDocumentChunkRepository(self._session)
        mapped = chunk_repo.map_indexed_chunks_by_ids(
            [chunk_id for chunk_id, _ in ranked_scores],
            doc_type=doc_type,
        )

        items: list[HrDocumentSearchResultItem] = []
        rank = 0
        for chunk_id, score in ranked_scores:
            row = mapped.get(chunk_id)
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
                    score=float(score),
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
        return items