"""Phase 3.2 Step 3 — integration tests for batch lookups and row validation."""

from __future__ import annotations

from sqlalchemy.orm import Session

from smarthris.importing.types import ParsedRow
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.importing.validation.row_validator import ImportRowValidator
from smarthris.models.employee import Employee, EmployeeStatus
from smarthris.repositories.department_repository import DepartmentRepository
from smarthris.repositories.employee_repository import EmployeeRepository


def _add_employee(
    session: Session,
    *,
    department_id: int,
    employee_number: str,
    work_email: str,
) -> None:
    session.add(
        Employee(
            employee_number=employee_number,
            first_name="Test",
            last_name="User",
            work_email=work_email,
            department_id=department_id,
            status=EmployeeStatus.ACTIVE,
        )
    )
    session.flush()


def test_department_get_by_name_case_insensitive(db_session: Session, sample_department) -> None:
    repo = DepartmentRepository(db_session)
    found = repo.get_by_name("  engineering ")
    assert found is not None
    assert found.id == sample_department.id
    assert repo.get_by_name("missing") is None


def test_department_list_all(db_session: Session, sample_department) -> None:
    repo = DepartmentRepository(db_session)
    repo.create("Sales")
    db_session.flush()
    names = {d.department_name for d in repo.list_all()}
    assert "Engineering" in names
    assert "Sales" in names


def test_find_existing_empty_does_not_query(db_session: Session, monkeypatch) -> None:
    repo = EmployeeRepository(db_session)
    calls = {"n": 0}
    original = db_session.scalars

    def counting_scalars(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(db_session, "scalars", counting_scalars)
    assert repo.find_existing_employee_numbers([]) == set()
    assert repo.find_existing_work_emails([]) == set()
    assert calls["n"] == 0


def test_find_existing_employee_numbers_and_emails(
    db_session: Session,
    sample_department,
) -> None:
    _add_employee(
        db_session,
        department_id=sample_department.id,
        employee_number="E-EXIST",
        work_email="exist@example.com",
    )
    repo = EmployeeRepository(db_session)
    assert repo.find_existing_employee_numbers(["E-EXIST", "E-NEW"]) == {"E-EXIST"}
    assert repo.find_existing_work_emails(["EXIST@example.com", "new@example.com"]) == {
        "exist@example.com"
    }


def test_find_existing_chunks_over_batch_size(
    db_session: Session,
    sample_department,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "smarthris.repositories.employee_repository.get_settings",
        lambda: type("S", (), {"import_batch_size": 10})(),
    )
    _add_employee(
        db_session,
        department_id=sample_department.id,
        employee_number="E-500",
        work_email="chunk@example.com",
    )
    repo = EmployeeRepository(db_session)
    execute_calls = {"n": 0}
    original_execute = db_session.execute
    original_scalars = db_session.scalars

    def counting_execute(*args, **kwargs):
        execute_calls["n"] += 1
        return original_execute(*args, **kwargs)

    def counting_scalars(*args, **kwargs):
        execute_calls["n"] += 1
        return original_scalars(*args, **kwargs)

    monkeypatch.setattr(db_session, "execute", counting_execute)
    monkeypatch.setattr(db_session, "scalars", counting_scalars)

    numbers = [f"E-{i}" for i in range(25)] + ["E-500"]
    found = repo.find_existing_employee_numbers(numbers)
    assert found == {"E-500"}
    # 26 values → ceil(26/10) = 3 chunks
    assert execute_calls["n"] == 3

    execute_calls["n"] = 0
    emails = [f"u{i}@example.com" for i in range(25)] + ["chunk@example.com"]
    assert repo.find_existing_work_emails(emails) == {"chunk@example.com"}
    assert execute_calls["n"] == 3


def test_row_validator_db_collision_and_success(
    db_session: Session,
    sample_department,
) -> None:
    _add_employee(
        db_session,
        department_id=sample_department.id,
        employee_number="E-DB",
        work_email="db@example.com",
    )
    validator = ImportRowValidator(
        department_repository=DepartmentRepository(db_session),
        employee_repository=EmployeeRepository(db_session),
        batch_size=50,
    )
    rows = [
        ParsedRow(
            row_number=2,
            values={
                "employee_number": "E-DB",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "work_email": "fresh@example.com",
                "department_id": str(sample_department.id),
            },
        ),
        ParsedRow(
            row_number=3,
            values={
                "employee_number": "E-NEW",
                "first_name": "Grace",
                "last_name": "Hopper",
                "work_email": "db@example.com",
                "department_name": "ENGINEERING",
            },
        ),
        ParsedRow(
            row_number=4,
            values={
                "employee_number": "E-OK",
                "first_name": "Alan",
                "last_name": "Turing",
                "work_email": "ok@example.com",
                "department_name": " engineering ",
            },
        ),
    ]
    results = list(validator.validate(iter(rows)))
    assert results[0].issues[0].code == ImportErrorCode.ALREADY_EXISTS.value
    assert results[0].issues[0].field == "employee_number"
    assert results[0].row_number == 2
    assert results[1].issues[0].code == ImportErrorCode.ALREADY_EXISTS.value
    assert results[1].issues[0].field == "work_email"
    assert results[2].is_valid
    assert results[2].validated is not None
    assert results[2].validated.resolved_department_id == sample_department.id
    assert validator.department_load_count == 1
