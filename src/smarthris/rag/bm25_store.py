"""Persisted BM25 keyword index over indexed HR document chunks."""

from __future__ import annotations

import json
from collections.abc import Sequence
import logging
import os
import pickle
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from smarthris.config import Settings
from smarthris.rag.embeddings import DocumentProcessingError
from smarthris.rag.faiss_store import ChunkVectorRecord, _index_update_lock

logger = logging.getLogger(__name__)

BM25_FORMAT_VERSION = 1
BM25_MANIFEST_NAME = "bm25_manifest.json"
BM25_CORPUS_NAME = "corpus.json"
BM25_INDEX_NAME = "bm25_index.pkl"
BM25_BACKUP_DIR_NAME = ".bm25_backup"

_TOKEN_PATTERN = re.compile(r"[a-z0-9']+")

_READ_BM25_CACHE: dict[str, tuple[str, _LoadedBm25Index | None]] = {}
_READ_BM25_CACHE_GUARD = threading.Lock()


@dataclass(frozen=True)
class Bm25Manifest:
    format_version: int
    chunk_count: int
    updated_at: str


@dataclass(frozen=True)
class Bm25SearchHit:
    chunk_id: int
    score: float


@dataclass
class _LoadedBm25Index:
    chunk_ids: list[int]
    bm25: Any


def tokenize_for_bm25(text: str) -> list[str]:
    """Lowercase alphanumeric/apostrophe tokens for BM25."""
    return _TOKEN_PATTERN.findall(text.lower())


def reset_bm25_cache_for_tests() -> None:
    """Clear in-memory BM25 cache (tests only)."""
    with _READ_BM25_CACHE_GUARD:
        _READ_BM25_CACHE.clear()


def bm25_live_dir(settings: Settings) -> Path:
    base = Path(settings.rag_index_dir).expanduser().resolve()
    return base / "bm25"


def bm25_manifest_path(settings: Settings) -> Path:
    return bm25_live_dir(settings) / BM25_MANIFEST_NAME


def bm25_corpus_path(settings: Settings) -> Path:
    return bm25_live_dir(settings) / BM25_CORPUS_NAME


def bm25_index_path(settings: Settings) -> Path:
    return bm25_live_dir(settings) / BM25_INDEX_NAME


def _import_bm25_okapi():
    try:
        from rank_bm25 import BM25Okapi
    except ImportError as exc:
        raise DocumentProcessingError(
            "BM25 keyword search requires the rank-bm25 package"
        ) from exc
    return BM25Okapi


def build_bm25_okapi(tokenized_corpus: list[list[str]]):
    """Construct a BM25Okapi instance (lazy import of rank-bm25)."""
    bm25_cls = _import_bm25_okapi()
    return bm25_cls(tokenized_corpus)


def load_bm25_manifest(path: Path) -> Bm25Manifest | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return Bm25Manifest(
            format_version=int(payload["format_version"]),
            chunk_count=int(payload["chunk_count"]),
            updated_at=str(payload.get("updated_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def validate_bm25_manifest(manifest: Bm25Manifest) -> None:
    if manifest.format_version != BM25_FORMAT_VERSION:
        raise DocumentProcessingError("BM25 index format version is incompatible")


def _write_manifest(path: Path, manifest: Bm25Manifest) -> None:
    payload = {
        "format_version": manifest.format_version,
        "chunk_count": manifest.chunk_count,
        "updated_at": manifest.updated_at,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _atomic_replace(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def _write_corpus_and_index(
    records: Sequence[ChunkVectorRecord],
    *,
    target_dir: Path,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    chunk_ids = [int(row.chunk_id) for row in records]
    corpus_payload = [
        {"chunk_id": int(row.chunk_id), "chunk_text": row.chunk_text} for row in records
    ]
    corpus_file = target_dir / BM25_CORPUS_NAME
    corpus_file.write_text(json.dumps(corpus_payload, indent=2), encoding="utf-8")

    tokenized = [tokenize_for_bm25(row.chunk_text) for row in records]
    if tokenized:
        bm25 = build_bm25_okapi(tokenized)
        with (target_dir / BM25_INDEX_NAME).open("wb") as handle:
            pickle.dump({"chunk_ids": chunk_ids, "bm25": bm25}, handle)
    else:
        if (target_dir / BM25_INDEX_NAME).exists():
            (target_dir / BM25_INDEX_NAME).unlink(missing_ok=True)

    manifest = Bm25Manifest(
        format_version=BM25_FORMAT_VERSION,
        chunk_count=len(records),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _write_manifest(target_dir / BM25_MANIFEST_NAME, manifest)


def _load_persisted_index(settings: Settings) -> _LoadedBm25Index | None:
    manifest = load_bm25_manifest(bm25_manifest_path(settings))
    corpus_path = bm25_corpus_path(settings)
    index_path = bm25_index_path(settings)
    if manifest is None or not corpus_path.is_file():
        return None
    try:
        validate_bm25_manifest(manifest)
    except DocumentProcessingError:
        logger.warning("Ignoring incompatible BM25 manifest")
        return None

    if manifest.chunk_count == 0:
        return _LoadedBm25Index(chunk_ids=[], bm25=None)

    if not index_path.is_file():
        logger.warning("BM25 corpus manifest exists but index file is missing")
        return None

    try:
        with index_path.open("rb") as handle:
            payload = pickle.load(handle)
        chunk_ids = [int(cid) for cid in payload["chunk_ids"]]
        bm25 = payload["bm25"]
    except Exception:
        logger.warning("Ignoring corrupt BM25 index file")
        return None

    return _LoadedBm25Index(chunk_ids=chunk_ids, bm25=bm25)


def _cache_fingerprint(settings: Settings) -> str:
    parts: list[str] = []
    for path in (bm25_manifest_path(settings), bm25_corpus_path(settings), bm25_index_path(settings)):
        if path.is_file():
            stat = path.stat()
            parts.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        else:
            parts.append(f"{path}:missing")
    return "|".join(parts)


def get_bm25_index(settings: Settings) -> _LoadedBm25Index | None:
    """Return a cached BM25 index loaded from disk, or None when unavailable."""
    fingerprint = _cache_fingerprint(settings)
    cache_key = str(bm25_live_dir(settings))

    with _READ_BM25_CACHE_GUARD:
        cached = _READ_BM25_CACHE.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

    with _index_update_lock(settings):
        fingerprint = _cache_fingerprint(settings)
        with _READ_BM25_CACHE_GUARD:
            cached = _READ_BM25_CACHE.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]

        loaded = _load_persisted_index(settings)
        with _READ_BM25_CACHE_GUARD:
            _READ_BM25_CACHE[cache_key] = (fingerprint, loaded)
        return loaded


def promote_bm25_directory(settings: Settings, candidate_dir: Path) -> None:
    """Atomically promote candidate BM25 files into the live directory."""
    required = [
        candidate_dir / BM25_MANIFEST_NAME,
        candidate_dir / BM25_CORPUS_NAME,
    ]
    if not all(path.is_file() for path in required):
        raise DocumentProcessingError("Candidate BM25 index is incomplete")

    live_dir = bm25_live_dir(settings)
    live_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=live_dir) as tmp_dir:
        tmp_path = Path(tmp_dir)
        for name in (BM25_MANIFEST_NAME, BM25_CORPUS_NAME, BM25_INDEX_NAME):
            source = candidate_dir / name
            if source.is_file():
                shutil.copy2(source, tmp_path / name)
                _atomic_replace(tmp_path / name, live_dir / name)


def rebuild_persisted_bm25_from_records(
    records: Sequence[ChunkVectorRecord],
    *,
    settings: Settings,
) -> None:
    """
    Rebuild and replace the live BM25 index from authoritative chunk records.

    Serialized with the same lock used for FAISS index updates.
    """
    with _index_update_lock(settings):
        live_dir = bm25_live_dir(settings)
        backup_dir = live_dir / BM25_BACKUP_DIR_NAME
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        backup_dir.mkdir(parents=True, exist_ok=True)
        had_live = bm25_manifest_path(settings).is_file()
        if had_live:
            for name in (BM25_MANIFEST_NAME, BM25_CORPUS_NAME, BM25_INDEX_NAME):
                source = live_dir / name
                if source.is_file():
                    shutil.copy2(source, backup_dir / name)

        candidate_dir = Path(tempfile.mkdtemp(prefix="bm25-candidate-", dir=live_dir))
        try:
            _write_corpus_and_index(records, target_dir=candidate_dir)
            promote_bm25_directory(settings, candidate_dir)
        except Exception as exc:
            if had_live:
                for name in (BM25_MANIFEST_NAME, BM25_CORPUS_NAME, BM25_INDEX_NAME):
                    source = backup_dir / name
                    if source.is_file():
                        shutil.copy2(source, live_dir / name)
            raise DocumentProcessingError("Failed to rebuild BM25 index") from exc
        finally:
            shutil.rmtree(candidate_dir, ignore_errors=True)
            shutil.rmtree(backup_dir, ignore_errors=True)
        cache_key = str(bm25_live_dir(settings))
        with _READ_BM25_CACHE_GUARD:
            _READ_BM25_CACHE.pop(cache_key, None)


def search_chunks_by_query(
    query: str,
    *,
    settings: Settings,
    top_k: int,
) -> tuple[bool, list[Bm25SearchHit]]:
    """
    Search the persisted BM25 index.

    Returns ``(index_ready, hits)`` with the same conventions as FAISS search.
    """
    if top_k < 1:
        raise DocumentProcessingError("top_k must be at least 1")

    tokens = tokenize_for_bm25(query)
    if not tokens:
        return True, []

    loaded = get_bm25_index(settings)
    if loaded is None:
        return False, []

    if loaded.bm25 is None or not loaded.chunk_ids:
        return True, []

    try:
        scores = loaded.bm25.get_scores(tokens)
    except Exception as exc:
        logger.warning("BM25 search failed")
        raise DocumentProcessingError("BM25 search failed") from exc

    ranked: list[Bm25SearchHit] = []
    for chunk_id, score in zip(loaded.chunk_ids, scores, strict=True):
        ranked.append(Bm25SearchHit(chunk_id=int(chunk_id), score=float(score)))

    ranked.sort(key=lambda hit: (-hit.score, hit.chunk_id))
    k = min(top_k, len(ranked))
    return True, ranked[:k]