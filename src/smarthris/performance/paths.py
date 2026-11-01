"""Filesystem locations for generated performance artifacts (outside the repo)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def performance_root_dir() -> Path:
    """
    Directory for generated CSVs and JSON results.

    Prefer ``SMART_HRIS_PERF_DIR``; otherwise use the system temp directory so
    artifacts are never written into the Git working tree by default.
    """
    override = os.environ.get("SMART_HRIS_PERF_DIR", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = Path(tempfile.gettempdir()).resolve() / "smarthris-ai-performance"
    root.mkdir(parents=True, exist_ok=True)
    return root


def performance_csv_dir() -> Path:
    path = performance_root_dir() / "csv"
    path.mkdir(parents=True, exist_ok=True)
    return path


def performance_results_dir() -> Path:
    path = performance_root_dir() / "results"
    path.mkdir(parents=True, exist_ok=True)
    return path
