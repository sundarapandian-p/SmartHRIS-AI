"""Add parent_job_id for import job retry lineage.

Revision ID: 20261101_0004
Revises: 20261101_0003
Create Date: 2026-11-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261101_0004"
down_revision: Union[str, Sequence[str], None] = "20261101_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "import_jobs",
        sa.Column("parent_job_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_import_jobs_parent_job_id",
        "import_jobs",
        "import_jobs",
        ["parent_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_import_jobs_parent_job_id",
        "import_jobs",
        ["parent_job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_jobs_parent_job_id", table_name="import_jobs")
    op.drop_constraint("fk_import_jobs_parent_job_id", "import_jobs", type_="foreignkey")
    op.drop_column("import_jobs", "parent_job_id")
