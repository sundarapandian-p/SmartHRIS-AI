"""Opt-in gate for large performance benchmarks."""

from __future__ import annotations

import os

PERFORMANCE_ENV_VAR = "SMART_HRIS_RUN_PERFORMANCE"


def performance_runs_enabled() -> bool:
    """Return True only when large benchmarks are explicitly enabled."""
    return os.environ.get(PERFORMANCE_ENV_VAR, "").strip() == "1"
