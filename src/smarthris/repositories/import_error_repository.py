"""Data access for bulk import row errors."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from smarthris.models.import_error import ImportError


class ImportErrorRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        import_job_id: str,
        row_number: int,
        code: str,
        message: str,
        employee_number: str | None = None,
        record_key: str | None = None,
        field: str | None = None,
    ) -> ImportError:
        row = ImportError(
            import_job_id=import_job_id,
            row_number=row_number,
            employee_number=employee_number,
            record_key=record_key,
            field=field,
            code=code,
            message=message,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def add_many(self, errors: list[ImportError]) -> None:
        self._session.add_all(errors)
        self._session.flush()

    def count_for_job(self, import_job_id: str) -> int:
        query = (
            select(func.count())
            .select_from(ImportError)
            .where(ImportError.import_job_id == import_job_id)
        )
        return int(self._session.scalar(query) or 0)

    def list_for_job(
        self,
        import_job_id: str,
        *,
        offset: int,
        limit: int,
    ) -> list[ImportError]:
        query = (
            select(ImportError)
            .where(ImportError.import_job_id == import_job_id)
            .order_by(ImportError.row_number, ImportError.id)
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(query).all())
