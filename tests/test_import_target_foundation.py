"""Phase 5.3.2C1 — import_target and record_key foundation tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from smarthris.models.import_error import ImportError
from smarthris.models.import_job import (
    ImportFileType,
    ImportJob,
    ImportJobStatus,
    ImportTarget,
)
from smarthris.repositories.import_error_repository import ImportErrorRepository
from smarthris.repositories.import_job_repository import ImportJobRepository
from smarthris.schemas.import_job import ImportErrorRead, ImportJobRead

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20261109_0007_import_target.py"
)


def test_import_target_enum_values() -> None:
    assert {member.value for member in ImportTarget} == {
        "employees",
        "courses",
        "enrolments",
    }


def test_import_job_defaults_to_employees(db_session: Session) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="employees.csv",
        file_type=ImportFileType.CSV,
    )
    db_session.flush()
    assert job.import_target is ImportTarget.EMPLOYEES
    assert job.status is ImportJobStatus.PENDING

    read = ImportJobRead.model_validate(job)
    assert read.import_target is ImportTarget.EMPLOYEES


def test_import_job_can_set_non_employee_target(db_session: Session) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(
        filename="courses.csv",
        file_type=ImportFileType.CSV,
        import_target=ImportTarget.COURSES,
    )
    db_session.flush()
    assert job.import_target is ImportTarget.COURSES


def test_record_key_optional_on_import_error(db_session: Session) -> None:
    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="employees.csv", file_type=ImportFileType.CSV)
    db_session.flush()

    errors = ImportErrorRepository(db_session)
    without_key = errors.add(
        import_job_id=job.id,
        row_number=2,
        code="invalid_value",
        message="bad row",
        employee_number="E-1",
    )
    with_key = errors.add(
        import_job_id=job.id,
        row_number=3,
        code="already_exists",
        message="course exists",
        record_key="SAFE-101",
        field="course_code",
    )
    db_session.flush()

    assert without_key.record_key is None
    assert with_key.record_key == "SAFE-101"

    read = ImportErrorRead.model_validate(with_key)
    assert read.record_key == "SAFE-101"
    assert ImportErrorRead.model_validate(without_key).record_key is None


def test_orm_default_on_plain_import_job_construct(db_session: Session) -> None:
    job = ImportJob(
        filename="plain.csv",
        file_type=ImportFileType.CSV,
    )
    db_session.add(job)
    db_session.flush()
    assert job.import_target is ImportTarget.EMPLOYEES

    err = ImportError(
        import_job_id=job.id,
        row_number=1,
        code="missing_required",
        message="missing",
    )
    db_session.add(err)
    db_session.flush()
    assert err.record_key is None
    assert err.employee_number is None


def test_migration_revision_chain() -> None:
    assert MIGRATION_PATH.is_file()
    namespace: dict[str, object] = {}
    exec(compile(MIGRATION_PATH.read_text(encoding="utf-8"), str(MIGRATION_PATH), "exec"), namespace)
    assert namespace["revision"] == "20261109_0007"
    assert namespace["down_revision"] == "20261109_0006"


def test_migration_is_additive_only() -> None:
    tree = ast.parse(MIGRATION_PATH.read_text(encoding="utf-8"))
    upgrade_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    upgrade_src = ast.unparse(upgrade_fn)
    assert "create_table" not in upgrade_src
    assert "drop_table" not in upgrade_src
    assert "alter_column" not in upgrade_src
    assert "rename_table" not in upgrade_src
    # server_default="employees" is expected; do not touch other domain tables.
    assert "courses" not in upgrade_src
    assert "course_enrolments" not in upgrade_src
    assert "hr_documents" not in upgrade_src
    assert "hr_document_chunks" not in upgrade_src
    assert "departments" not in upgrade_src
    assert "import_jobs" in upgrade_src
    assert "import_errors" in upgrade_src
    assert "import_target" in upgrade_src
    assert "record_key" in upgrade_src
    assert "add_column" in upgrade_src
