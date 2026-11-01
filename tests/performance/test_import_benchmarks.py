"""
Opt-in import processing benchmarks.

Skipped unless SMART_HRIS_RUN_PERFORMANCE=1.
Do not enable this in normal CI. Large 10K/50K/100K runs are manual.
"""

from __future__ import annotations

import pytest

from smarthris.config import get_settings
from smarthris.performance.benchmark_runner import run_processing_benchmark
from smarthris.performance.db_guard import assert_safe_performance_database_url


@pytest.mark.performance
def test_opt_in_smoke_processing_benchmark_small() -> None:
    """
    Tiny smoke (5 rows) validating the harness against MySQL test/bench DB.

    Explicitly not a 10K/50K/100K scale run.
    """
    get_settings.cache_clear()
    database_url = assert_safe_performance_database_url(
        get_settings().sqlalchemy_test_database_url
    )
    result = run_processing_benchmark(
        row_count=5,
        database_url=database_url,
        dry_run=False,
        invalid_percent=0.0,
    )
    assert result.row_count == 5
    assert result.job_status == "completed"
    assert result.success_count == 5
    assert result.failed_count == 0
    assert result.csv_file_size_bytes > 0
    assert result.database_name in {"smarthris_test", "smarthris_bench"}
