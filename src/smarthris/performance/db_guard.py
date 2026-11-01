"""Refuse performance runs against non-test / non-benchmark MySQL databases."""

from __future__ import annotations

from urllib.parse import urlparse

APPROVED_PERFORMANCE_DATABASE_NAMES: frozenset[str] = frozenset(
    {
        "smarthris_test",
        "smarthris_bench",
    }
)


class UnsafePerformanceDatabaseError(RuntimeError):
    """Raised when a performance run targets an unsafe database URL."""


def assert_safe_performance_database_url(database_url: str | None) -> str:
    """
    Validate that ``database_url`` points at an approved MySQL test/bench DB.

    Never allows SQLite or production-style database names.
    """
    if not database_url or not str(database_url).strip():
        raise UnsafePerformanceDatabaseError(
            "Performance database URL is missing. Set TEST_DATABASE_URL "
            "(or an explicit MySQL URL) to an approved test/benchmark database."
        )

    url = str(database_url).strip()
    lowered = url.lower()
    if lowered.startswith("sqlite:") or "sqlite" in lowered.split(":", 1)[0]:
        raise UnsafePerformanceDatabaseError(
            "Refusing performance runs against SQLite. Use MySQL 8.4 test/bench only."
        )

    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if "mysql" not in scheme and "mariadb" not in scheme:
        raise UnsafePerformanceDatabaseError(
            f"Refusing performance runs with non-MySQL scheme {parsed.scheme!r}."
        )

    path = (parsed.path or "").lstrip("/")
    database_name = path.split("?")[0] if path else ""
    if not database_name:
        raise UnsafePerformanceDatabaseError(
            f"Could not parse database name from URL: {url!r}"
        )

    if database_name not in APPROVED_PERFORMANCE_DATABASE_NAMES:
        raise UnsafePerformanceDatabaseError(
            f"Refusing performance run on database {database_name!r}. "
            f"Approved names: {sorted(APPROVED_PERFORMANCE_DATABASE_NAMES)}."
        )

    return url
