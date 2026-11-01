"""Add import_jobs and import_errors tables for bulk employee import.

Revision ID: 20261101_0003
Revises: 20261027_0002
Create Date: 2026-11-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261101_0003"
down_revision: Union[str, Sequence[str], None] = "20261027_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

import_file_type = sa.Enum("csv", "xlsx", name="import_file_type")
import_job_status = sa.Enum("pending", "running", "completed", "failed", name="import_job_status")


def upgrade() -> None:
    import_file_type.create(op.get_bind(), checkfirst=True)
    import_job_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            import_job_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", import_file_type, nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_status", "import_jobs", ["status"])

    op.create_table(
        "import_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_job_id", sa.String(length=36), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("employee_number", sa.String(length=50), nullable=True),
        sa.Column("field", sa.String(length=100), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_errors_import_job_id", "import_errors", ["import_job_id"])
    op.create_index(
        "ix_import_errors_job_row",
        "import_errors",
        ["import_job_id", "row_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_errors_job_row", table_name="import_errors")
    op.drop_index("ix_import_errors_import_job_id", table_name="import_errors")
    op.drop_table("import_errors")
    op.drop_index("ix_import_jobs_status", table_name="import_jobs")
    op.drop_table("import_jobs")
    import_job_status.drop(op.get_bind(), checkfirst=True)
    import_file_type.drop(op.get_bind(), checkfirst=True)
