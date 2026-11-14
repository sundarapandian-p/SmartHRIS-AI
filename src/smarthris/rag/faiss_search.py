"""Read-only FAISS similarity search over persisted chunk vectors."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
import faiss
import numpy as np

from smarthris.config import Settings
from smarthris.rag.embeddings import DocumentProcessingError
from smarthris.rag.faiss_store import (
    _index_update_lock,
    _model_slug,
    index_path,
    load_index_if_compatible,
    manifest_path,
)

logger = logging.getLogger(__name__)

_READ_INDEX_CACHE: dict[str, tuple[str, int, faiss.IndexIDMap2 | None]] = {}
_READ_INDEX_CACHE_GUARD = threading.Lock()


@dataclass(frozen=True)
class FaissSearchHit:
    """One chunk match from vector search (higher score is more similar)."""

    chunk_id: int
    score: float


def reset_search_index_cache_for_tests() -> None:
    """Clear in-memory search index cache (tests only)."""
    with _READ_INDEX_CACHE_GUARD:
        _READ_INDEX_CACHE.clear()


def _index_cache_fingerprint(settings: Settings) -> str:
    manifest = manifest_path(settings)
    index = index_path(settings)
    parts: list[str] = []
    for path in (manifest, index):
        if path.is_file():
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        else:
            parts.append(f"{path}:missing")
    return "|".join(parts)


def get_search_index(
    settings: Settings,
    *,
    dimension: int,
) -> faiss.IndexIDMap2 | None:
    """
    Return a compatible in-memory index for search, using a lightweight cache.

    Cache entries invalidate when on-disk index or manifest changes. Loads are
    serialized with the same per-model lock used by index publishing to avoid
    reading partially replaced files in the single-worker deployment.
    """
    slug = _model_slug(settings)
    fingerprint = _index_cache_fingerprint(settings)

    with _READ_INDEX_CACHE_GUARD:
        cached = _READ_INDEX_CACHE.get(slug)
        if cached is not None and cached[0] == fingerprint and cached[1] == dimension:
            return cached[2]

    with _index_update_lock(settings):
        fingerprint = _index_cache_fingerprint(settings)
        with _READ_INDEX_CACHE_GUARD:
            cached = _READ_INDEX_CACHE.get(slug)
            if cached is not None and cached[0] == fingerprint and cached[1] == dimension:
                return cached[2]

        loaded = load_index_if_compatible(settings, dimension=dimension)
        with _READ_INDEX_CACHE_GUARD:
            _READ_INDEX_CACHE[slug] = (fingerprint, dimension, loaded)
        return loaded


def search_chunks_by_vector(
    query_vector: np.ndarray,
    *,
    settings: Settings,
    dimension: int,
    top_k: int,
) -> tuple[bool, list[FaissSearchHit]]:
    """
    Search the persisted FAISS index for chunk IDs similar to ``query_vector``.

    Returns ``(index_ready, hits)``:
    - ``index_ready`` is False when the index is missing or incompatible.
    - ``index_ready`` is True with empty hits when the index exists but has no vectors.
    """
    if top_k < 1:
        raise DocumentProcessingError("top_k must be at least 1")

    vectors = np.asarray(query_vector, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    if vectors.shape[0] != 1:
        raise DocumentProcessingError("Query embedding must be a single vector")
    if vectors.shape[1] != dimension:
        raise DocumentProcessingError("Query embedding dimension mismatch")

    index = get_search_index(settings, dimension=dimension)
    if index is None:
        return False, []

    ntotal = int(index.ntotal)
    if ntotal == 0:
        return True, []

    k = min(top_k, ntotal)
    try:
        scores, ids = index.search(vectors, k)
    except Exception as exc:
        logger.warning("FAISS search failed")
        raise DocumentProcessingError("Vector search failed") from exc

    hits: list[FaissSearchHit] = []
    for score, chunk_id in zip(scores[0], ids[0], strict=True):
        if int(chunk_id) < 0:
            continue
        hits.append(FaissSearchHit(chunk_id=int(chunk_id), score=float(score)))
    return True, hits