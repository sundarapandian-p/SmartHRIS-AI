"""Focused tests for benchmark cleanup-on-failure behavior."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from smarthris.models.import_job import ImportJobStatus
from smarthris.performance.benchmark_runner import (
    BenchmarkResult,
    run_processing_benchmark,
)
from smarthris.performance.csv_generator import GeneratedCsv
from smarthris.performance.metrics import MemorySample

SAFE_URL = "mysql+pymysql://u:p@127.0.0.1:3306/smarthris_test"
JOB_ID = "11111111-1111-1111-1111-111111111111"


def _generated(path: Path) -> GeneratedCsv:
    return GeneratedCsv(
        path=path,
        row_count=3,
        file_size_bytes=128,
        employee_number_prefix="PERF-TEST",
        invalid_row_count=0,
        department_id=1,
    )


@contextmanager
def _benchmark_stubs(
    *,
    process_side_effect=None,
    result_write_error: BaseException | None = None,
    job_csv: Path,
):
    engine = MagicMock()
    session = MagicMock()
    session_factory = MagicMock(return_value=session)
    department = SimpleNamespace(id=42)
    job = SimpleNamespace(
        status=ImportJobStatus.COMPLETED,
        success_count=3,
        failed_count=0,
    )

    def measure(fn, sample_rss=True):
        fn()
        return 0.01, MemorySample(peak_rss_bytes=None, psutil_available=False)

    jobs_repo = MagicMock()
    jobs_repo.get_by_id.return_value = job
    errors_repo = MagicMock()
    errors_repo.count_for_job.return_value = 0

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.assert_safe_performance_database_url",
                return_value=SAFE_URL,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.create_db_engine",
                return_value=engine,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.verify_database_connection",
                return_value=True,
            )
        )
        stack.enter_context(
            patch("smarthris.performance.benchmark_runner.Base.metadata.create_all")
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.create_session_factory",
                return_value=session_factory,
            )
        )
        stack.enter_context(
            patch("smarthris.performance.benchmark_runner.ensure_import_temp_dir")
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.new_job_id",
                return_value=JOB_ID,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.import_storage_path",
                return_value=job_csv,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner._ensure_department",
                return_value=department,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.generate_employee_csv",
                side_effect=lambda **kwargs: _generated(Path(kwargs["destination"])),
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.ImportJobRepository",
                return_value=jobs_repo,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.ImportErrorRepository",
                return_value=errors_repo,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.process_import_job",
                side_effect=process_side_effect,
            )
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.measure_callable",
                side_effect=measure,
            )
        )
        stack.enter_context(
            patch("smarthris.performance.benchmark_runner.shutil.rmtree")
        )
        stack.enter_context(
            patch(
                "smarthris.performance.benchmark_runner.performance_results_dir",
                return_value=job_csv.parent,
            )
        )
        if result_write_error is not None:
            stack.enter_context(
                patch.object(Path, "write_text", side_effect=result_write_error)
            )
        else:
            stack.enter_context(
                patch.object(Path, "write_text", return_value=None)
            )
        yield


def test_cleanup_runs_when_process_import_job_raises(tmp_path: Path) -> None:
    job_csv = tmp_path / "job.csv"
    with (
        _benchmark_stubs(process_side_effect=RuntimeError("worker boom"), job_csv=job_csv),
        patch("smarthris.performance.benchmark_runner._cleanup_run") as cleanup,
    ):
        with pytest.raises(RuntimeError, match="worker boom"):
            run_processing_benchmark(row_count=3, database_url=SAFE_URL)
    cleanup.assert_called_once()
    assert cleanup.call_args.kwargs["job_id"] == JOB_ID
    assert cleanup.call_args.kwargs["employee_number_prefix"].startswith("PERF-")


def test_cleanup_runs_when_result_json_write_fails(tmp_path: Path) -> None:
    job_csv = tmp_path / "job.csv"
    with (
        _benchmark_stubs(
            process_side_effect=None,
            result_write_error=OSError("disk full"),
            job_csv=job_csv,
        ),
        patch("smarthris.performance.benchmark_runner._cleanup_run") as cleanup,
    ):
        with pytest.raises(OSError, match="disk full"):
            run_processing_benchmark(row_count=3, database_url=SAFE_URL)
    cleanup.assert_called_once()


def test_cleanup_skipped_when_keep_artifacts_true(tmp_path: Path) -> None:
    job_csv = tmp_path / "job.csv"
    with (
        _benchmark_stubs(process_side_effect=None, job_csv=job_csv),
        patch("smarthris.performance.benchmark_runner._cleanup_run") as cleanup,
    ):
        result = run_processing_benchmark(
            row_count=3,
            database_url=SAFE_URL,
            keep_artifacts=True,
        )
    cleanup.assert_not_called()
    assert isinstance(result, BenchmarkResult)
    assert result.success_count == 3
    assert result.job_status == ImportJobStatus.COMPLETED.value


def test_original_process_exception_preserved_when_cleanup_fails(tmp_path: Path) -> None:
    job_csv = tmp_path / "job.csv"
    with (
        _benchmark_stubs(process_side_effect=RuntimeError("worker boom"), job_csv=job_csv),
        patch(
            "smarthris.performance.benchmark_runner._cleanup_run",
            side_effect=RuntimeError("cleanup boom"),
        ),
    ):
        with pytest.raises(RuntimeError, match="worker boom") as exc_info:
            run_processing_benchmark(row_count=3, database_url=SAFE_URL)
    notes = getattr(exc_info.value, "__notes__", [])
    assert any("cleanup also failed" in note for note in notes)


def test_cleanup_failure_reported_when_run_succeeds(tmp_path: Path) -> None:
    job_csv = tmp_path / "job.csv"
    with (
        _benchmark_stubs(process_side_effect=None, job_csv=job_csv),
        patch(
            "smarthris.performance.benchmark_runner._cleanup_run",
            side_effect=RuntimeError("cleanup boom"),
        ),
    ):
        with pytest.raises(RuntimeError, match="Benchmark completed but cleanup failed"):
            run_processing_benchmark(row_count=3, database_url=SAFE_URL)
