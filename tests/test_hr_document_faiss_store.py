"""Tests for FAISS index persistence."""

from __future__ import annotations

import numpy as np

from smarthris.config import Settings
from smarthris.rag.faiss_store import (
    ChunkVectorRecord,
    build_empty_index,
    load_index_if_compatible,
    manifest_path,
    rebuild_index_from_records,
)


def test_faiss_persist_reload_and_rebuild(tmp_path) -> None:
    settings = Settings(
        _env_file=None,
        rag_index_dir=str(tmp_path / "indexes"),
        embedding_model_name="unit-test-model",
    )

    def embed(texts):
        vectors = np.zeros((len(texts), 4), dtype=np.float32)
        for i in range(len(texts)):
            vectors[i, 0] = 1.0
        return vectors

    records = [
        ChunkVectorRecord(chunk_id=10, chunk_text="first"),
        ChunkVectorRecord(chunk_id=11, chunk_text="second"),
    ]
    rebuild_index_from_records(records, settings=settings, dimension=4, embed_fn=embed)
    loaded = load_index_if_compatible(settings, dimension=4)
    assert loaded is not None
    assert loaded.ntotal == 2

    empty = build_empty_index(4)
    assert empty.ntotal == 0
    assert manifest_path(settings).is_file()
