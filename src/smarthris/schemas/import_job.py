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
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
