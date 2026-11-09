"""Add import_target to import_jobs and record_key to import_errors.

Revision ID: 20261109_0007
Revises: 20261109_0006
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_0007"
down_revision: Union[str, Sequence[str], None] = "20261109_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

import_target = sa.Enum(
    "employees",
    "courses",
    "enrolments",
    name="import_target",
)


def upgrade() -> None:
    import_target.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "import_jobs",
        sa.Column(
            "import_target",
            import_target,
            nullable=False,
            server_default="employees",
        ),
    )
    op.create_index("ix_import_jobs_import_target", "import_jobs", ["import_target"])

    op.add_column(
        "import_errors",
        sa.Column("record_key", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("import_errors", "record_key")
    op.drop_index("ix_import_jobs_import_target", table_name="import_jobs")
    op.drop_column("import_jobs", "import_target")
    import_target.drop(op.get_bind(), checkfirst=True)
