"""Phase 3.8 — import job retry (child job + CSV copy + concurrency)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from smarthris.api.deps import get_db
from smarthris.config import Settings
from smarthris.core.exceptions import ConflictError, StorageError
from smarthris.db.session import create_session_factory
from smarthris.importing.storage import (
    copy_import_file,
    delete_import_file,
    import_storage_path,
)
from smarthris.main import create_app
from smarthris.models.import_job import ImportFileType, ImportJob, ImportJobStatus
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.schemas.import_job import ImportJobRead
from smarthris.services.import_upload_service import ImportUploadService
from tests.auth_helpers import TEST_JWT_SECRET, attach_active_user_override


def _csv_body() -> str:
    return (
        "Employee Number,First Name,Last Name,Work Email,Department ID\n"
        "R-1,Ada,Lovelace,retry1@example.com,1\n"
    )


def _write_job_csv(settings: Settings, job_id: str, body: str | None = None) -> Path:
    path = import_storage_path(settings.import_temp_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or _csv_body(), encoding="utf-8")
    return path


@pytest.fixture
def import_settings(tmp_path: Path, integration_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        app_name="SmartHRIS-Import-Retry",
        database_url=integration_database_url,
        import_temp_dir=str(tmp_path / "imports"),
        max_upload_bytes=200_000,
        jwt_secret_key=TEST_JWT_SECRET,
    )


@pytest.fixture
def import_api_client(
    db_session: Session,
    import_settings: Settings,
    hr_test_user,
) -> TestClient:
    app = create_app(import_settings)

    def override_get_db():
        try:
            yield db_session
        except Exception:
            db_session.rollback()
            raise

    app.dependency_overrides[get_db] = override_get_db
    attach_active_user_override(app, hr_test_user)
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _prepare_completed_partial(
    db_session: Session,
    settings: Settings,
    *,
    dry_run: bool = False,
    failed_count: int = 2,
) -> ImportJob:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="partial.csv",
        file_type=ImportFileType.CSV,
        dry_run=dry_run,
    )
    jobs.mark_running(job)
    jobs.update_counts(
        job,
        total_rows=5,
        processed_rows=5,
        success_count=3,
        failed_count=failed_count,
    )
    jobs.mark_completed(job)
    db_session.commit()
    _write_job_csv(settings, job.id)
    return job


def test_retry_completed_partial_creates_child(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings)
    parent_path = import_storage_path(import_settings.import_temp_dir, parent.id)
    parent_bytes = parent_path.read_bytes()

    with patch(
        "smarthris.api.routers.imports.process_import_job"
    ) as mock_process:
        response = import_api_client.post(f"/imports/{parent.id}/retry")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] != parent.id
    assert body["parent_job_id"] == parent.id
    assert body["dry_run"] is False
    assert body["status"] == ImportJobStatus.PENDING.value
    assert body["filename"] == "partial.csv"
    assert body["file_type"] == "csv"
    assert body["total_rows"] == 0
    assert body["processed_rows"] == 0
    assert body["success_count"] == 0
    assert body["failed_count"] == 0
    mock_process.assert_called_once()
    assert mock_process.call_args.args[0] == body["id"]

    child_path = import_storage_path(import_settings.import_temp_dir, body["id"])
    assert child_path.is_file()
    assert child_path.read_bytes() == parent_bytes
    assert parent_path.is_file()
    assert parent_path.read_bytes() == parent_bytes


def test_retry_failed_job(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    parent = jobs.create(filename="failed.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(parent)
    jobs.mark_failed(parent, error_message="CSV parse error: bad headers")
    db_session.commit()
    _write_job_csv(import_settings, parent.id)

    with patch("smarthris.api.routers.imports.process_import_job"):
        response = import_api_client.post(f"/imports/{parent.id}/retry")
    assert response.status_code == 201
    assert response.json()["parent_job_id"] == parent.id


def test_reject_dry_run_source(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings, dry_run=True)
    response = import_api_client.post(f"/imports/{parent.id}/retry")
    assert response.status_code == 409
    assert response.json()["code"] == "conflict"


def test_reject_pending_and_running(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    pending = jobs.create(filename="p.csv", file_type=ImportFileType.CSV)
    db_session.commit()
    _write_job_csv(import_settings, pending.id)
    assert import_api_client.post(f"/imports/{pending.id}/retry").status_code == 409

    running = jobs.create(filename="r.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(running)
    db_session.commit()
    _write_job_csv(import_settings, running.id)
    assert import_api_client.post(f"/imports/{running.id}/retry").status_code == 409


def test_reject_successful_completed(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(
        db_session, import_settings, failed_count=0
    )
    response = import_api_client.post(f"/imports/{parent.id}/retry")
    assert response.status_code == 409


def test_reject_missing_source_file(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    jobs = ImportJobRepository(db_session)
    parent = jobs.create(filename="nofile.csv", file_type=ImportFileType.CSV)
    jobs.mark_running(parent)
    jobs.update_counts(parent, total_rows=1, processed_rows=1, failed_count=1)
    jobs.mark_completed(parent)
    db_session.commit()
    # Intentionally do not write CSV.

    response = import_api_client.post(f"/imports/{parent.id}/retry")
    assert response.status_code == 409
    assert ImportJobRepository(db_session).get_by_id(parent.id) is not None
    children = db_session.scalars(
        select(ImportJob).where(ImportJob.parent_job_id == parent.id)
    ).all()
    assert children == []


def test_unknown_job_404(import_api_client: TestClient) -> None:
    response = import_api_client.post(
        "/imports/00000000-0000-0000-0000-000000000099/retry"
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_parent_unchanged_and_errors_preserved(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings)
    errors = ImportErrorRepository(db_session)
    errors.add(
        import_job_id=parent.id,
        row_number=2,
        code="invalid_email",
        message="parent error",
        employee_number="E1",
        field="work_email",
    )
    db_session.commit()

    before = ImportJobRepository(db_session).get_by_id(parent.id)
    assert before is not None
    snapshot = (
        before.status,
        before.total_rows,
        before.processed_rows,
        before.success_count,
        before.failed_count,
        before.error_message,
        before.finished_at,
    )

    with patch("smarthris.api.routers.imports.process_import_job"):
        child_body = import_api_client.post(f"/imports/{parent.id}/retry").json()

    db_session.refresh(before)
    assert (
        before.status,
        before.total_rows,
        before.processed_rows,
        before.success_count,
        before.failed_count,
        before.error_message,
        before.finished_at,
    ) == snapshot
    assert ImportErrorRepository(db_session).count_for_job(parent.id) == 1
    assert ImportErrorRepository(db_session).count_for_job(child_body["id"]) == 0


def test_active_child_blocks_second_retry(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings)
    with patch("smarthris.api.routers.imports.process_import_job"):
        first = import_api_client.post(f"/imports/{parent.id}/retry")
        second = import_api_client.post(f"/imports/{parent.id}/retry")
    assert first.status_code == 201
    assert second.status_code == 409


def test_exclusive_copy_does_not_overwrite(tmp_path: Path) -> None:
    src = tmp_path / "src.csv"
    dest = tmp_path / "dest.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    dest.write_text("existing\n", encoding="utf-8")
    with pytest.raises(StorageError, match="already exists"):
        copy_import_file(src, dest)
    assert dest.read_text(encoding="utf-8") == "existing\n"


def test_db_failure_cleans_up_copied_file(
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings)
    service = ImportUploadService(db_session, import_settings)

    with patch.object(
        ImportJobRepository,
        "create",
        side_effect=RuntimeError("db boom"),
    ):
        with pytest.raises(RuntimeError, match="db boom"):
            service.create_retry(parent.id)

    # No child row; no leftover files except parent.
    children = db_session.scalars(
        select(ImportJob).where(ImportJob.parent_job_id == parent.id)
    ).all()
    assert children == []
    imports_dir = Path(import_settings.import_temp_dir)
    csv_files = list(imports_dir.glob("*.csv"))
    assert len(csv_files) == 1
    assert csv_files[0].name == f"{parent.id}.csv"


def test_child_status_and_errors_endpoints(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings)
    with patch("smarthris.api.routers.imports.process_import_job"):
        child_id = import_api_client.post(f"/imports/{parent.id}/retry").json()["id"]

    status = import_api_client.get(f"/imports/{child_id}")
    assert status.status_code == 200
    assert status.json()["parent_job_id"] == parent.id
    ImportJobRead.model_validate(status.json())

    errors = ImportErrorRepository(db_session)
    errors.add(
        import_job_id=child_id,
        row_number=3,
        code="already_exists",
        message="child only",
        employee_number="C1",
        field="employee_number",
    )
    db_session.commit()

    err_body = import_api_client.get(f"/imports/{child_id}/errors").json()
    assert err_body["total"] == 1
    assert err_body["items"][0]["message"] == "child only"


def test_background_task_scheduled_only_after_commit(
    import_api_client: TestClient,
    db_session: Session,
    import_settings: Settings,
) -> None:
    parent = _prepare_completed_partial(db_session, import_settings)
    order: list[str] = []

    original_commit = db_session.commit

    def tracking_commit():
        order.append("commit")
        return original_commit()

    def tracking_process(job_id, **kwargs):
        order.append(f"schedule:{job_id}")

    with (
        patch.object(db_session, "commit", side_effect=tracking_commit),
        patch(
            "smarthris.api.routers.imports.process_import_job",
            side_effect=tracking_process,
        ),
    ):
        response = import_api_client.post(f"/imports/{parent.id}/retry")

    assert response.status_code == 201
    child_id = response.json()["id"]
    assert "commit" in order
    assert f"schedule:{child_id}" in order
    assert order.index("commit") < order.index(f"schedule:{child_id}")


def test_concurrent_retry_one_winner_mysql(
    integration_engine,
    import_settings: Settings,
) -> None:
    """
    Two separate MySQL sessions racing create_retry for the same parent.

    Requires real commits (not the savepoint-wrapped db_session fixture).
    """
    factory = create_session_factory(integration_engine)
    setup = factory()
    parent_id: str
    try:
        jobs = ImportJobRepository(setup)
        parent = jobs.create(filename="race.csv", file_type=ImportFileType.CSV)
        jobs.mark_running(parent)
        jobs.update_counts(
            parent,
            total_rows=4,
            processed_rows=4,
            success_count=2,
            failed_count=2,
        )
        jobs.mark_completed(parent)
        setup.commit()
        parent_id = parent.id
        _write_job_csv(import_settings, parent_id)
    finally:
        setup.close()

    results: list[tuple[str, str | None]] = []

    def attempt() -> None:
        session = factory()
        try:
            service = ImportUploadService(session, import_settings)
            child = service.create_retry(parent_id)
            results.append(("ok", child.id))
        except ConflictError:
            results.append(("conflict", None))
        except Exception as exc:  # pragma: no cover - unexpected
            results.append(("error", str(exc)))
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt), pool.submit(attempt)]
        for fut in futures:
            fut.result(timeout=30)

    oks = [r for r in results if r[0] == "ok"]
    conflicts = [r for r in results if r[0] == "conflict"]
    assert len(results) == 2, results
    assert len(oks) == 1, results
    assert len(conflicts) == 1, results

    winner_id = oks[0][1]
    assert winner_id is not None

    verify = factory()
    try:
        children = list(
            verify.scalars(
                select(ImportJob).where(ImportJob.parent_job_id == parent_id)
            ).all()
        )
        assert len(children) == 1
        assert children[0].id == winner_id
        assert children[0].status == ImportJobStatus.PENDING

        child_path = import_storage_path(import_settings.import_temp_dir, winner_id)
        assert child_path.is_file()
        # Losing request must not leave an extra pending CSV besides parent+winner.
        csv_names = {
            p.name for p in Path(import_settings.import_temp_dir).glob("*.csv")
        }
        assert csv_names == {f"{parent_id}.csv", f"{winner_id}.csv"}
    finally:
        # Cleanup committed rows so later tests are not polluted.
        for row in list(
            verify.scalars(
                select(ImportJob).where(
                    (ImportJob.id == parent_id) | (ImportJob.parent_job_id == parent_id)
                )
            ).all()
        ):
            delete_import_file(
                import_storage_path(import_settings.import_temp_dir, row.id)
            )
            verify.delete(row)
        verify.commit()
        verify.close()
