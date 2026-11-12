"""Persisted FAISS vector index (IndexIDMap2 + IndexFlatIP)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import faiss
import numpy as np

from smarthris.config import Settings
from smarthris.rag.embeddings import DocumentProcessingError

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index.faiss"
BACKUP_DIR_NAME = ".live_backup"

_INDEX_UPDATE_LOCKS: dict[str, threading.Lock] = {}
_INDEX_LOCK_GUARD = threading.Lock()

"""
Consistency notes (MySQL is authoritative; on-disk FAISS is derived):

1. Candidate indexes are built in a temp directory; the live index is backed up
   first and is not overwritten until MySQL has committed chunk/metadata changes.
2. There is still a crash window after a successful MySQL commit and before
   promote completes: the database may show ``indexed`` while FAISS is stale.
   Use ``rebuild_persisted_index_from_records`` (or the processing reindex API)
   to reconcile from MySQL.
3. If promote fails after MySQL committed, the previous live index is restored
   from backup and the document should be marked failed for reindex.

Concurrency (document indexing / reconciliation):

``_index_update_lock`` uses a **process-local** ``threading.Lock`` per embedding
model. It serializes index backup, build, promote, and reconcile **within one
Python process only** — not across separate OS processes.

Run the API with **exactly one Uvicorn worker** (``--workers 1`` or the default
single process from ``smarthris-api`` / ``uvicorn`` without ``--workers``) when
HR document indexing or ``reconcile_vector_index_from_database`` is enabled.
Multiple worker processes can corrupt on-disk FAISS by promoting concurrently;
this deployment does not use a cross-process file lock.
"""


@dataclass(frozen=True)
class FaissManifest:
    embedding_model_name: str
    dimension: int
    vector_count: int
    updated_at: str


@dataclass(frozen=True)
class ChunkVectorRecord:
    chunk_id: int
    chunk_text: str


@dataclass(frozen=True)
class LiveIndexBackup:
    """Paths to a recoverable snapshot of the previous live index."""

    backup_dir: Path
    had_live_index: bool


def model_index_dir(settings: Settings) -> Path:
    """Return a filesystem-safe directory for one embedding model."""
    slug = _model_slug(settings)
    base = Path(settings.rag_index_dir).expanduser().resolve()
    return base / slug


def _model_slug(settings: Settings) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", settings.embedding_model_name)


def _index_update_lock(settings: Settings) -> threading.Lock:
    """
    Return the per-model update lock (process-local ``threading.Lock``).

    Multiple Uvicorn worker processes each hold a separate lock instance; only one
    API worker must serve traffic when FAISS indexing is active (see module docstring).
    """
    slug = _model_slug(settings)
    with _INDEX_LOCK_GUARD:
        lock = _INDEX_UPDATE_LOCKS.get(slug)
        if lock is None:
            lock = threading.Lock()
            _INDEX_UPDATE_LOCKS[slug] = lock
        return lock


def manifest_path(settings: Settings) -> Path:
    return model_index_dir(settings) / MANIFEST_NAME


def index_path(settings: Settings) -> Path:
    return model_index_dir(settings) / INDEX_NAME


def load_manifest(path: Path) -> FaissManifest | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return FaissManifest(
            embedding_model_name=str(payload["embedding_model_name"]),
            dimension=int(payload["dimension"]),
            vector_count=int(payload["vector_count"]),
            updated_at=str(payload.get("updated_at", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def validate_manifest(manifest: FaissManifest, settings: Settings, dimension: int) -> None:
    if manifest.embedding_model_name != settings.embedding_model_name:
        raise DocumentProcessingError("FAISS index was built with a different embedding model")
    if manifest.dimension != dimension:
        raise DocumentProcessingError("FAISS index dimension does not match the embedding model")


def load_index_if_compatible(
    settings: Settings,
    *,
    dimension: int,
) -> faiss.IndexIDMap2 | None:
    """Load an on-disk index when manifest matches settings; otherwise None."""
    manifest = load_manifest(manifest_path(settings))
    path = index_path(settings)
    if manifest is None or not path.is_file():
        return None
    try:
        validate_manifest(manifest, settings, dimension)
        index = faiss.read_index(str(path))
    except Exception:
        logger.warning("Ignoring corrupt or incompatible FAISS index")
        return None
    if not isinstance(index, faiss.IndexIDMap2):
        return None
    return index


def _write_manifest(path: Path, manifest: FaissManifest) -> None:
    payload = {
        "embedding_model_name": manifest.embedding_model_name,
        "dimension": manifest.dimension,
        "vector_count": manifest.vector_count,
        "updated_at": manifest.updated_at,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _atomic_replace(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = destination.with_suffix(destination.suffix + ".tmp")
    if tmp_name.exists():
        tmp_name.unlink(missing_ok=True)
    os.replace(source, destination)


def _write_index_pair(
    index: faiss.IndexIDMap2,
    *,
    target_dir: Path,
    settings: Settings,
    dimension: int,
) -> None:
    """Write ``index.faiss`` and ``manifest.json`` into ``target_dir`` (not live)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    index_file = target_dir / INDEX_NAME
    manifest_file = target_dir / MANIFEST_NAME
    faiss.write_index(index, str(index_file))
    manifest = FaissManifest(
        embedding_model_name=settings.embedding_model_name,
        dimension=dimension,
        vector_count=int(index.ntotal),
        updated_at=datetime.now(UTC).isoformat(),
    )
    _write_manifest(manifest_file, manifest)


def backup_live_index(settings: Settings) -> LiveIndexBackup:
    """
    Snapshot the current live index (if any) without modifying it.

    Used to recover when promotion fails after MySQL committed.
    """
    live_dir = model_index_dir(settings)
    backup_dir = live_dir / BACKUP_DIR_NAME
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    live_index = index_path(settings)
    live_manifest = manifest_path(settings)
    had_live = live_index.is_file() and live_manifest.is_file()
    if had_live:
        shutil.copy2(live_index, backup_dir / INDEX_NAME)
        shutil.copy2(live_manifest, backup_dir / MANIFEST_NAME)

    return LiveIndexBackup(backup_dir=backup_dir, had_live_index=had_live)


def restore_live_index_from_backup(settings: Settings, backup: LiveIndexBackup) -> None:
    """Restore the live index files from a prior ``backup_live_index`` snapshot."""
    if not backup.had_live_index:
        index_path(settings).unlink(missing_ok=True)
        manifest_path(settings).unlink(missing_ok=True)
        return
    source_index = backup.backup_dir / INDEX_NAME
    source_manifest = backup.backup_dir / MANIFEST_NAME
    if not source_index.is_file() or not source_manifest.is_file():
        logger.warning("Live index backup is incomplete; skipping restore")
        return
    live_dir = model_index_dir(settings)
    live_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=live_dir) as tmp_dir:
        tmp_path = Path(tmp_dir)
        shutil.copy2(source_index, tmp_path / INDEX_NAME)
        shutil.copy2(source_manifest, tmp_path / MANIFEST_NAME)
        _atomic_replace(tmp_path / INDEX_NAME, index_path(settings))
        _atomic_replace(tmp_path / MANIFEST_NAME, manifest_path(settings))


def _remove_backup_dir(backup: LiveIndexBackup) -> None:
    shutil.rmtree(backup.backup_dir, ignore_errors=True)


def promote_index_directory(settings: Settings, candidate_dir: Path) -> None:
    """Atomically promote candidate ``index.faiss``/manifest into the live index paths."""
    candidate_index = candidate_dir / INDEX_NAME
    candidate_manifest = candidate_dir / MANIFEST_NAME
    if not candidate_index.is_file() or not candidate_manifest.is_file():
        raise DocumentProcessingError("Candidate FAISS index is incomplete")

    live_dir = model_index_dir(settings)
    live_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=live_dir) as tmp_dir:
        tmp_path = Path(tmp_dir)
        shutil.copy2(candidate_index, tmp_path / INDEX_NAME)
        shutil.copy2(candidate_manifest, tmp_path / MANIFEST_NAME)
        _atomic_replace(tmp_path / INDEX_NAME, index_path(settings))
        _atomic_replace(tmp_path / MANIFEST_NAME, manifest_path(settings))


def build_empty_index(dimension: int) -> faiss.IndexIDMap2:
    base = faiss.IndexFlatIP(dimension)
    return faiss.IndexIDMap2(base)


def build_index_in_memory(
    records: Sequence[ChunkVectorRecord],
    *,
    dimension: int,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
    batch_size: int = 32,
) -> faiss.IndexIDMap2:
    index = build_empty_index(dimension)
    if not records:
        return index

    ids: list[int] = []
    texts: list[str] = []
    for row in records:
        ids.append(int(row.chunk_id))
        texts.append(row.chunk_text)

    for start in range(0, len(texts), batch_size):
        batch_texts = texts[start : start + batch_size]
        batch_ids = np.array(ids[start : start + batch_size], dtype=np.int64)
        vectors = embed_fn(batch_texts)
        if vectors.shape[0] != len(batch_texts):
            raise DocumentProcessingError("Embedding batch size mismatch")
        if vectors.shape[1] != dimension:
            raise DocumentProcessingError("Embedding dimension mismatch")
        index.add_with_ids(vectors.astype(np.float32), batch_ids)
    return index


def build_index_files_in_directory(
    records: Sequence[ChunkVectorRecord],
    *,
    directory: Path,
    settings: Settings,
    dimension: int,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
    batch_size: int = 32,
) -> None:
    """Materialize a complete FAISS index under ``directory`` (does not touch live files)."""
    index = build_index_in_memory(
        records,
        dimension=dimension,
        embed_fn=embed_fn,
        batch_size=batch_size,
    )
    _write_index_pair(index, target_dir=directory, settings=settings, dimension=dimension)


def publish_index_after_mysql_commit(
    records: Sequence[ChunkVectorRecord],
    *,
    settings: Settings,
    dimension: int,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
    batch_size: int,
    mysql_commit: Callable[[], None],
) -> None:
    """
    Build a candidate index, commit MySQL via ``mysql_commit``, then promote.

    Serialized per embedding model. On failure:
    - Before ``mysql_commit``: live index unchanged.
    - After ``mysql_commit`` but before/during promote: restore live backup.
    """
    live_dir = model_index_dir(settings)
    live_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir: Path | None = None
    backup: LiveIndexBackup | None = None
    mysql_committed = False

    with _index_update_lock(settings):
        backup = backup_live_index(settings)
        try:
            candidate_dir = Path(
                tempfile.mkdtemp(prefix="faiss-candidate-", dir=live_dir)
            )
            build_index_files_in_directory(
                records,
                directory=candidate_dir,
                settings=settings,
                dimension=dimension,
                embed_fn=embed_fn,
                batch_size=batch_size,
            )
            mysql_commit()
            mysql_committed = True
            promote_index_directory(settings, candidate_dir)
        except Exception as exc:
            if mysql_committed and backup is not None:
                restore_live_index_from_backup(settings, backup)
            raise DocumentProcessingError("Failed to publish FAISS index") from exc
        finally:
            if candidate_dir is not None:
                shutil.rmtree(candidate_dir, ignore_errors=True)
            if backup is not None:
                _remove_backup_dir(backup)


def rebuild_persisted_index_from_records(
    records: Sequence[ChunkVectorRecord],
    *,
    settings: Settings,
    dimension: int,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
    batch_size: int = 32,
) -> None:
    """
    Rebuild and replace the live index from authoritative MySQL chunk rows.

    Safe recovery when the database and on-disk index may have diverged.
    """
    with _index_update_lock(settings):
        backup = backup_live_index(settings)
        candidate_dir = Path(tempfile.mkdtemp(prefix="faiss-rebuild-", dir=model_index_dir(settings)))
        try:
            build_index_files_in_directory(
                records,
                directory=candidate_dir,
                settings=settings,
                dimension=dimension,
                embed_fn=embed_fn,
                batch_size=batch_size,
            )
            promote_index_directory(settings, candidate_dir)
        except Exception as exc:
            restore_live_index_from_backup(settings, backup)
            raise DocumentProcessingError("Failed to rebuild FAISS index") from exc
        finally:
            shutil.rmtree(candidate_dir, ignore_errors=True)
            _remove_backup_dir(backup)


def rebuild_index_from_records(
    records: Sequence[ChunkVectorRecord],
    *,
    settings: Settings,
    dimension: int,
    embed_fn: Callable[[Sequence[str]], np.ndarray],
    batch_size: int = 32,
) -> faiss.IndexIDMap2:
    """
    Backward-compatible helper: rebuild live index from records (serialized).

    Prefer ``publish_index_after_mysql_commit`` during document processing.
    """
    rebuild_persisted_index_from_records(
        records,
        settings=settings,
        dimension=dimension,
        embed_fn=embed_fn,
        batch_size=batch_size,
    )
    loaded = load_index_if_compatible(settings, dimension=dimension)
    if loaded is None:
        return build_empty_index(dimension)
    return loaded
