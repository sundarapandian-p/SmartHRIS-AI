"""Reciprocal Rank Fusion for hybrid retrieval."""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    *ranked_chunk_id_lists: Sequence[int],
    k: int,
) -> list[tuple[int, float]]:
    """
    Merge ranked chunk-id lists using RRF: sum(1 / (k + rank)).

    Ranks are 1-based within each list. Duplicate chunk IDs across lists accumulate
    score. Tie-breaking is deterministic (higher score first, then lower chunk id).
    """
    if k < 1:
        raise ValueError("RRF constant k must be at least 1")

    scores: dict[int, float] = {}
    for ranked_ids in ranked_chunk_id_lists:
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            cid = int(chunk_id)
            scores[cid] = scores.get(cid, 0.0) + (1.0 / (k + rank))

    merged = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [(int(chunk_id), float(score)) for chunk_id, score in merged]