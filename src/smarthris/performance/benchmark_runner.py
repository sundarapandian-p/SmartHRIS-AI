"""Opt-in import processing benchmark runner (MySQL test/bench DB only)."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from smarthris.config import Settings
from smarthris.db.base import Base
import smarthris.models  # noqa: F401 — register ORM metadata
from smarthris.db.session import create_db_engine, create_session_factory, verify_database_connection
from smarthris.importing.storage import (
    delete_import_file,
    ensure_import_temp_dir,
    import_storage_path,
    new_job_id,
)
from smarthris.models.department import Department
from smarthris.models.employee import Employee
from smarthris.models.import_error import ImportError
from smarthris.models.import_job import ImportFileType, ImportJob, ImportJobStatus
from smarthris.performance.csv_generator import generate_employee_csv
from smarthris.performance.db_guard import assert_safe_performance_database_url
from smarthris.performance.metrics import measure_callable
from smarthris.performance.paths import performance_results_dir
from smarthris.repositories.department_repository import DepartmentRepository
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.services.import_processing_service import process_import_job


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    run_id: str
    started_at: str
    finished_at: str
    duration_seconds: float
    peak_rss_bytes: int | None
    psutil_available: bool
    row_count: int
    csv_path: str
    csv_file_size_bytes: int
    job_id: str
    job_status: str
    success_count: int
    failed_count: int
    error_count: int
    dry_run: bool
    invalid_percent: float
    import_batch_size: int
    database_name: str
    results_json_path: str


def _database_name_from_url(database_url: str) -> str:
    from urllib.parse import urlparse

    path = (urlparse(database_url).path or "").lstrip("/")
    return path.split("?")[0]


def _ensure_department(session: Session, *, name: str) -> Department:
    repo = DepartmentRepository(session)
    existing = repo.get_by_name(name)
    if existing is not None:
        return existing
    department = repo.create(name, description="Performance benchmark department")
    session.flush()
    return department


def _cleanup_run(
    session: Session,
    *,
    job_id: str,
    employee_number_prefix: str,
    csv_path: Path,
    job_storage_path: Path,
    created_department_id: int | None,
) -> None:
    """Delete only rows/files created by this run (requires prior DB URL guard)."""
    session.execute(
        delete(Employee).where(Employee.employee_number.like(f"{employee_number_prefix}-%"))
    )
    session.execute(delete(ImportError).where(ImportError.import_job_id == job_id))
    session.execute(delete(ImportJob).where(ImportJob.id == job_id))
    if created_department_id is not None:
        # Only remove the department when no employees still reference it.
        remaining = session.scalar(
            select(func.count())
            .select_from(Employee)
            .where(Employee.department_id == created_department_id)
        )
        if int(remaining or 0) == 0:
            session.execute(delete(Department).where(Department.id == created_department_id))
    session.commit()
    delete_import_file(job_storage_path)
    if csv_path.resolve() != job_storage_path.resolve():
        delete_import_file(csv_path)


def run_processing_benchmark(
    *,
    row_count: int,
    database_url: str,
    dry_run: bool = False,
    invalid_percent: float = 0.0,
    inject_duplicate_every: int | None = None,
    import_batch_size: int | None = None,
    keep_artifacts: bool = False,
) -> BenchmarkResult:
    """
    Create an ImportJob, run ``process_import_job``, record metrics, then clean up.

    Cleanup runs in ``finally`` unless ``keep_artifacts`` is True, including when
    processing or result persistence fails. Does not exercise the HTTP upload path.
    """
    # Guard before any engine work or cleanup side effects.
    safe_url = assert_safe_performance_database_url(database_url)
    run_id = uuid4().hex[:12]
    prefix = f"PERF-{run_id}"

    settings = Settings(
        _env_file=None,
        database_url=safe_url,
        import_temp_dir=str(performance_results_dir().parent / "import-temp" / run_id),
        import_batch_size=import_batch_size or Settings().import_batch_size,
        app_name="SmartHRIS-Performance",
    )
    engine = create_db_engine(settings)
    if not verify_database_connection(engine):
        engine.dispose()
        raise RuntimeError("Performance database is not reachable")
    # Safe: URL already approved as test/bench. create_all is additive only.
    Base.metadata.create_all(engine)

    session_factory = create_session_factory(engine)
    ensure_import_temp_dir(settings.import_temp_dir)

    job_id = new_job_id()
    job_csv_path = import_storage_path(settings.import_temp_dir, job_id)
    created_department_id: int | None = None
    generated_path: Path | None = None
    result: BenchmarkResult | None = None
    run_error: BaseException | None = None
    cleanup_error: BaseException | None = None

    try:
        bootstrap = session_factory()
        try:
            department = _ensure_department(bootstrap, name=f"PERF-DEPT-{run_id}")
            created_department_id = department.id
            bootstrap.commit()
            department_id = department.id
        finally:
            bootstrap.close()

        generated = generate_employee_csv(
            row_count=row_count,
            department_id=department_id,
            destination=job_csv_path,
            employee_number_prefix=prefix,
            invalid_percent=invalid_percent,
            inject_duplicate_every=inject_duplicate_every,
            settings=settings,
            run_id=run_id,
        )
        generated_path = generated.path

        setup = session_factory()
        try:
            jobs = ImportJobRepository(setup)
            jobs.create(
                filename=generated.path.name,
                file_type=ImportFileType.CSV,
                dry_run=dry_run,
                job_id=job_id,
            )
            setup.commit()
        finally:
            setup.close()

        started_at = datetime.now(timezone.utc)

        def _run() -> None:
            process_import_job(
                job_id,
                session_factory=session_factory,
                settings=settings,
            )

        duration, memory = measure_callable(_run)
        finished_at = datetime.now(timezone.utc)

        result_session = session_factory()
        try:
            job = ImportJobRepository(result_session).get_by_id(job_id)
            if job is None:
                raise RuntimeError(f"Import job {job_id} missing after processing")
            error_count = ImportErrorRepository(result_session).count_for_job(job_id)
            results_path = performance_results_dir() / f"{run_id}-{row_count}.perf.json"
            result = BenchmarkResult(
                run_id=run_id,
                started_at=started_at.isoformat(),
                finished_at=finished_at.isoformat(),
                duration_seconds=duration,
                peak_rss_bytes=memory.peak_rss_bytes,
                psutil_available=memory.psutil_available,
                row_count=row_count,
                csv_path=str(generated.path),
                csv_file_size_bytes=generated.file_size_bytes,
                job_id=job_id,
                job_status=job.status.value
                if isinstance(job.status, ImportJobStatus)
                else str(job.status),
                success_count=int(job.success_count),
                failed_count=int(job.failed_count),
                error_count=int(error_count),
                dry_run=dry_run,
                invalid_percent=invalid_percent,
                import_batch_size=settings.import_batch_size,
                database_name=_database_name_from_url(safe_url),
                results_json_path=str(results_path),
            )
            results_path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
        finally:
            result_session.close()
    except BaseException as exc:
        run_error = exc
    finally:
        if not keep_artifacts:
            try:
                cleanup = session_factory()
                try:
                    _cleanup_run(
                        cleanup,
                        job_id=job_id,
                        employee_number_prefix=prefix,
                        csv_path=generated_path or job_csv_path,
                        job_storage_path=job_csv_path,
                        created_department_id=created_department_id,
                    )
                finally:
                    cleanup.close()
                temp_root = Path(settings.import_temp_dir)
                if temp_root.exists():
                    shutil.rmtree(temp_root, ignore_errors=True)
            except BaseException as exc:
                cleanup_error = exc
        engine.dispose()

    if run_error is not None:
        if cleanup_error is not None:
            run_error.add_note(f"Benchmark cleanup also failed: {cleanup_error!r}")
        raise run_error
    if cleanup_error is not None:
        raise RuntimeError(
            f"Benchmark completed but cleanup failed: {cleanup_error}"
        ) from cleanup_error
    if result is None:
        raise RuntimeError("Benchmark produced no result")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SmartHRIS-AI import processing benchmark")
    parser.add_argument("--rows", type=int, required=True, help="Employee data rows to generate")
    parser.add_argument(
        "--database-url",
        default=None,
        help="MySQL URL (default: Settings.sqlalchemy_test_database_url)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--invalid-percent", type=float, default=0.0)
    parser.add_argument("--duplicate-every", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Skip cleanup of employees/jobs/files (debug only)",
    )
    args = parser.parse_args(argv)

    from smarthris.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    database_url = args.database_url or settings.sqlalchemy_test_database_url

    result = run_processing_benchmark(
        row_count=args.rows,
        database_url=database_url,
        dry_run=args.dry_run,
        invalid_percent=args.invalid_percent,
        inject_duplicate_every=args.duplicate_every,
        import_batch_size=args.batch_size,
        keep_artifacts=args.keep_artifacts,
    )
    print(json.dumps(asdict(result), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
