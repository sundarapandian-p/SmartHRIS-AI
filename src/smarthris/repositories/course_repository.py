"""Data access for LMS courses."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from smarthris.config import get_settings
from smarthris.models.course import Course


def _unique_trimmed(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in values:
        value = raw.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


class CourseRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, course: Course) -> Course:
        self._session.add(course)
        return course

    def add_many(self, courses: Sequence[Course]) -> None:
        if not courses:
            return
        self._session.add_all(list(courses))

    def find_existing_course_codes(self, codes: Sequence[str]) -> set[str]:
        """Return course_code values that already exist (chunked lookups)."""
        unique_codes = _unique_trimmed(codes)
        if not unique_codes:
            return set()

        batch_size = get_settings().import_batch_size
        found: set[str] = set()
        for start in range(0, len(unique_codes), batch_size):
            chunk = unique_codes[start : start + batch_size]
            rows = self._session.scalars(
                select(Course.course_code).where(Course.course_code.in_(chunk))
            ).all()
            found.update(rows)
        return found

    def count(self) -> int:
        from sqlalchemy import func

        return int(self._session.scalar(select(func.count()).select_from(Course)) or 0)
