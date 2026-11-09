"""Data access for LMS course enrolments."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from smarthris.config import get_settings
from smarthris.models.course_enrolment import CourseEnrolment


class CourseEnrolmentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, enrolment: CourseEnrolment) -> CourseEnrolment:
        self._session.add(enrolment)
        return enrolment

    def add_many(self, enrolments: Sequence[CourseEnrolment]) -> None:
        if not enrolments:
            return
        self._session.add_all(list(enrolments))

    def find_existing_pairs(
        self,
        pairs: Sequence[tuple[int, int]],
    ) -> set[tuple[int, int]]:
        """
        Return ``(employee_id, course_id)`` pairs that already exist.

        Queries in chunks of ``import_batch_size``.
        """
        unique_pairs: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        for pair in pairs:
            if pair in seen:
                continue
            seen.add(pair)
            unique_pairs.append(pair)
        if not unique_pairs:
            return set()

        batch_size = get_settings().import_batch_size
        found: set[tuple[int, int]] = set()
        for start in range(0, len(unique_pairs), batch_size):
            chunk = unique_pairs[start : start + batch_size]
            rows = self._session.execute(
                select(CourseEnrolment.employee_id, CourseEnrolment.course_id).where(
                    tuple_(CourseEnrolment.employee_id, CourseEnrolment.course_id).in_(chunk)
                )
            ).all()
            found.update((int(employee_id), int(course_id)) for employee_id, course_id in rows)
        return found

    def count(self) -> int:
        from sqlalchemy import func

        return int(
            self._session.scalar(select(func.count()).select_from(CourseEnrolment)) or 0
        )
