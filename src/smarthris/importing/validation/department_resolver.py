"""Resolve import department_id / department_name using an in-memory cache."""

from __future__ import annotations

from dataclasses import dataclass

from smarthris.importing.types import ImportRowIssue
from smarthris.importing.validation.error_codes import ImportErrorCode
from smarthris.models.department import Department
from smarthris.repositories.department_repository import (
    DepartmentRepository,
    normalize_department_name,
)


@dataclass(frozen=True, slots=True)
class DepartmentResolveResult:
    department_id: int | None
    issues: tuple[ImportRowIssue, ...]


class DepartmentResolver:
    """
    Load departments once and resolve rows without further DB lookups.

    Name matching is case-insensitive and whitespace-normalized.
    """

    def __init__(self, department_repository: DepartmentRepository) -> None:
        self._by_id: dict[int, Department] = {}
        self._by_name: dict[str, Department] = {}
        self._load_count = 0
        self._load(department_repository)

    @property
    def load_count(self) -> int:
        """How many times ``list_all`` was invoked (expected: 1 per resolver)."""
        return self._load_count

    def _load(self, department_repository: DepartmentRepository) -> None:
        departments = department_repository.list_all()
        self._load_count += 1
        for department in departments:
            self._by_id[department.id] = department
            key = normalize_department_name(department.department_name)
            # First wins if collation ever allowed near-duplicates; unique DB constraint
            # normally prevents this.
            self._by_name.setdefault(key, department)

    def resolve(
        self,
        *,
        row_number: int,
        department_id: int | None,
        department_name: str | None,
        employee_number: str | None,
    ) -> DepartmentResolveResult:
        issues: list[ImportRowIssue] = []
        by_id: Department | None = None
        by_name: Department | None = None

        if department_id is not None:
            by_id = self._by_id.get(department_id)
            if by_id is None:
                issues.append(
                    ImportRowIssue(
                        row_number=row_number,
                        code=ImportErrorCode.UNKNOWN_DEPARTMENT.value,
                        message=f"Unknown department_id {department_id}",
                        field="department_id",
                        employee_number=employee_number,
                    )
                )

        if department_name is not None:
            key = normalize_department_name(department_name)
            by_name = self._by_name.get(key) if key else None
            if by_name is None:
                issues.append(
                    ImportRowIssue(
                        row_number=row_number,
                        code=ImportErrorCode.UNKNOWN_DEPARTMENT.value,
                        message=f"Unknown department_name '{department_name.strip()}'",
                        field="department_name",
                        employee_number=employee_number,
                    )
                )

        if issues:
            return DepartmentResolveResult(department_id=None, issues=tuple(issues))

        if by_id is not None and by_name is not None and by_id.id != by_name.id:
            return DepartmentResolveResult(
                department_id=None,
                issues=(
                    ImportRowIssue(
                        row_number=row_number,
                        code=ImportErrorCode.DEPARTMENT_MISMATCH.value,
                        message=(
                            f"department_id {department_id} does not match "
                            f"department_name '{department_name}'"
                        ),
                        field="department",
                        employee_number=employee_number,
                    ),
                ),
            )

        resolved = by_id or by_name
        if resolved is None:
            # Schema should prevent this; keep a defensive issue.
            return DepartmentResolveResult(
                department_id=None,
                issues=(
                    ImportRowIssue(
                        row_number=row_number,
                        code=ImportErrorCode.MISSING_REQUIRED.value,
                        message="Provide department_id and/or department_name",
                        field="department",
                        employee_number=employee_number,
                    ),
                ),
            )

        return DepartmentResolveResult(department_id=resolved.id, issues=())
