"""API schemas for bulk employee import jobs."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from smarthris.models.import_job import ImportFileType, ImportJobStatus


class ImportJobRead(BaseModel):
    """Public import job representation (no filesystem paths)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: ImportJobStatus
    filename: str
    file_type: ImportFileType
    dry_run: bool
    total_rows: int
    processed_rows: int
    success_count: int
    failed_count: int
    error_message: str | None
    parent_job_id: str | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ImportErrorRead(BaseModel):
    """One persisted import row error (no job path or internal metadata)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    row_number: int
    employee_number: str | None
    field: str | None
    code: str
    message: str


class ImportErrorListResponse(BaseModel):
    """Paginated import errors for a single job."""

    import_job_id: str
    items: list[ImportErrorRead]
    total: int
    page: int
    page_size: int
    pages: int
