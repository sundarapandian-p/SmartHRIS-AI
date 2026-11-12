"""Lazy Sentence-Transformer embeddings for document chunks."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

import numpy as np

from smarthris.config import Settings


class EmbeddingProvider(Protocol):
    """Protocol for injecting mock embedders in tests."""

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


_encoder_singleton: object | None = None
_encoder_model_name: str | None = None


class DocumentProcessingError(Exception):
    """Embedding or index preparation failure."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def _load_sentence_transformer(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def get_embedding_encoder(settings: Settings) -> EmbeddingProvider:
    """Return a cached SentenceTransformer for ``settings.embedding_model_name``."""
    global _encoder_singleton, _encoder_model_name
    model_name = settings.embedding_model_name
    if _encoder_singleton is None or _encoder_model_name != model_name:
        _encoder_singleton = _load_sentence_transformer(model_name)
        _encoder_model_name = model_name
    return _encoder_singleton  # type: ignore[return-value]


def reset_embedding_cache_for_tests() -> None:
    """Clear the lazy singleton (tests only)."""
    global _encoder_singleton, _encoder_model_name
    _encoder_singleton = None
    _encoder_model_name = None


def encode_texts(
    texts: Sequence[str],
    *,
    settings: Settings,
    provider: EmbeddingProvider | None = None,
    batch_size: int = 32,
) -> np.ndarray:
    """
    Encode and L2-normalize texts for inner-product cosine search.

    Raises ``DocumentProcessingError`` when vectors are invalid.
    """
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)

    encoder = provider or get_embedding_encoder(settings)
    raw = encoder.encode(list(texts), batch_size=batch_size, show_progress_bar=False)
    vectors = np.asarray(raw, dtype=np.float32)
    if vectors.ndim != 2:
        raise DocumentProcessingError("Embedding model returned an unexpected tensor shape")
    if not np.all(np.isfinite(vectors)):
        raise DocumentProcessingError("Embedding model returned non-finite values")

    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    normalized = vectors / norms
    return normalized.astype(np.float32, copy=False)


def embedding_dimension(
    settings: Settings,
    provider: EmbeddingProvider | None = None,
) -> int:
    """Probe vector width using a minimal encode (prefer provider in tests)."""
    vectors = encode_texts(["dimension probe"], settings=settings, provider=provider)
    if vectors.shape[0] != 1 or vectors.shape[1] < 1:
        raise DocumentProcessingError("Unable to determine embedding dimension")
    return int(vectors.shape[1])


# Type alias for test injection.
EmbedTextsFn = Callable[..., np.ndarray]
