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
MIGRATION_0008_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "20261109_0008_widen_import_error_record_key.py"
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


def test_record_key_column_length_allows_enrolment_composite() -> None:
    column = ImportError.__table__.c.record_key
    assert column.type.length == 101


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
    max_key = ("E" * 50) + "|" + ("C" * 50)
    assert len(max_key) == 101
    max_length_key = errors.add(
        import_job_id=job.id,
        row_number=4,
        code="already_exists",
        message="enrolment pair exists",
        record_key=max_key,
        field="employee_number",
        employee_number="E" * 50,
    )
    db_session.flush()

    assert without_key.record_key is None
    assert with_key.record_key == "SAFE-101"
    assert max_length_key.record_key == max_key
    assert len(max_length_key.record_key) == 101

    read = ImportErrorRead.model_validate(with_key)
    assert read.record_key == "SAFE-101"
    assert ImportErrorRead.model_validate(without_key).record_key is None
    assert ImportErrorRead.model_validate(max_length_key).record_key == max_key


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

    assert MIGRATION_0008_PATH.is_file()
    namespace_0008: dict[str, object] = {}
    exec(
        compile(
            MIGRATION_0008_PATH.read_text(encoding="utf-8"),
            str(MIGRATION_0008_PATH),
            "exec",
        ),
        namespace_0008,
    )
    assert namespace_0008["revision"] == "20261109_0008"
    assert namespace_0008["down_revision"] == "20261109_0007"


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


def _load_migration_0008_namespace() -> dict[str, object]:
    namespace: dict[str, object] = {}
    exec(
        compile(
            MIGRATION_0008_PATH.read_text(encoding="utf-8"),
            str(MIGRATION_0008_PATH),
            "exec",
        ),
        namespace,
    )
    return namespace


def test_migration_0008_widens_record_key_only() -> None:
    tree = ast.parse(MIGRATION_0008_PATH.read_text(encoding="utf-8"))
    upgrade_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"
    )
    downgrade_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "downgrade"
    )
    upgrade_src = ast.unparse(upgrade_fn)
    downgrade_src = ast.unparse(downgrade_fn)
    source = MIGRATION_0008_PATH.read_text(encoding="utf-8")

    assert "alter_column" in upgrade_src
    assert "record_key" in upgrade_src
    assert "101" in upgrade_src
    assert "create_table" not in upgrade_src
    assert "drop_table" not in upgrade_src
    assert "drop_column" not in upgrade_src
    assert "import_jobs" not in upgrade_src
    assert "courses" not in upgrade_src
    assert "course_enrolments" not in upgrade_src

    assert "alter_column" in downgrade_src
    assert "100" in downgrade_src
    assert "assert_safe_to_narrow_record_key" in downgrade_src
    assert "LEFT(" not in source
    assert "UPDATE import_errors" not in source


def test_migration_0008_downgrade_refuses_overlength_record_key(
    db_session: Session,
) -> None:
    namespace = _load_migration_0008_namespace()
    assert_safe = namespace["assert_safe_to_narrow_record_key"]
    assert callable(assert_safe)

    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="enrolments.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    ImportErrorRepository(db_session).add(
        import_job_id=job.id,
        row_number=2,
        code="already_exists",
        message="max key",
        record_key=("E" * 50) + "|" + ("C" * 50),
    )
    db_session.flush()

    with pytest.raises(RuntimeError, match="exceed 100 characters"):
        assert_safe(db_session.connection())


def test_migration_0008_downgrade_allows_keys_within_100(
    db_session: Session,
) -> None:
    namespace = _load_migration_0008_namespace()
    assert_safe = namespace["assert_safe_to_narrow_record_key"]
    assert callable(assert_safe)

    jobs = ImportJobRepository(db_session)
    job = jobs.create(filename="courses.csv", file_type=ImportFileType.CSV)
    db_session.flush()
    ImportErrorRepository(db_session).add(
        import_job_id=job.id,
        row_number=2,
        code="already_exists",
        message="short key",
        record_key="SAFE-101",
    )
    ImportErrorRepository(db_session).add(
        import_job_id=job.id,
        row_number=3,
        code="invalid_value",
        message="null key",
    )
    db_session.flush()

    assert_safe(db_session.connection())
