"""Environment-aware AppEmailStr validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from smarthris.config import get_settings
from smarthris.core.email import validate_app_email
from smarthris.importing.validation.row_schema import EmployeeImportRow
from smarthris.models.employee import EmployeeStatus
from smarthris.models.user import UserRole
from smarthris.schemas.auth import CurrentUserResponse, LoginRequest
from smarthris.schemas.employee import EmployeeRead


@pytest.fixture
def set_app_env(monkeypatch: pytest.MonkeyPatch):
    def _set(env: str) -> None:
        monkeypatch.setenv("APP_ENV", env)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


@pytest.mark.parametrize("app_env", ("development", "staging", "production"))
def test_example_com_accepted_in_all_environments(
    set_app_env, app_env: str
) -> None:
  set_app_env(app_env)
  assert validate_app_email("hr.person@example.com") == "hr.person@example.com"


@pytest.mark.parametrize("app_env", ("development", "staging"))
def test_example_test_accepted_outside_production(set_app_env, app_env: str) -> None:
  set_app_env(app_env)
  assert (
      validate_app_email("avery.synth.stg@example.test")
      == "avery.synth.stg@example.test"
  )


def test_example_test_rejected_in_production(set_app_env) -> None:
  set_app_env("production")
  with pytest.raises(ValueError, match="special-use|reserved"):
    validate_app_email("avery.synth.stg@example.test")


def test_malformed_email_rejected(set_app_env) -> None:
  set_app_env("development")
  with pytest.raises(ValueError):
    validate_app_email("not-an-email")


def test_email_normalization_trim_and_lowercase(set_app_env) -> None:
  set_app_env("development")
  assert validate_app_email("  User@Example.COM  ") == "user@example.com"


def test_employee_read_accepts_synthetic_staging_email(set_app_env) -> None:
  set_app_env("staging")
  now = datetime.now(timezone.utc)
  employee = EmployeeRead(
      id=1,
      employee_number="STG0001",
      first_name="Avery",
      last_name="Synth",
      work_email="avery.synth.stg@example.test",
      department_id=1,
      job_role=None,
      job_title=None,
      manager_employee_id=None,
      hire_date=None,
      status=EmployeeStatus.ACTIVE,
      created_at=now,
      updated_at=now,
  )
  assert employee.work_email == "avery.synth.stg@example.test"


def test_login_request_accepts_synthetic_staging_email_in_staging(set_app_env) -> None:
  set_app_env("staging")
  req = LoginRequest(email="hr.staging@example.test", password="Secret1!")
  assert req.email == "hr.staging@example.test"


def test_current_user_response_accepts_example_test_in_staging(set_app_env) -> None:
  set_app_env("staging")
  user = CurrentUserResponse(
      id=2,
      email="employee.staging@example.test",
      role=UserRole.EMPLOYEE,
      employee_id=3,
      is_active=True,
      last_login_at=None,
  )
  assert user.email == "employee.staging@example.test"


def test_employee_import_row_accepts_example_test_in_development(set_app_env) -> None:
  set_app_env("development")
  row = EmployeeImportRow.model_validate(
      {
          "employee_number": "STG0099",
          "first_name": "Test",
          "last_name": "User",
          "work_email": "row.user@example.test",
          "department_id": 1,
      }
  )
  assert row.work_email == "row.user@example.test"


def test_employee_create_rejects_example_test_in_production(set_app_env) -> None:
  from smarthris.schemas.employee import EmployeeCreate

  set_app_env("production")
  with pytest.raises(ValidationError):
      EmployeeCreate.model_validate(
          {
              "employee_number": "E1",
              "first_name": "A",
              "last_name": "B",
              "work_email": "bad@example.test",
              "department_id": 1,
          }
      )