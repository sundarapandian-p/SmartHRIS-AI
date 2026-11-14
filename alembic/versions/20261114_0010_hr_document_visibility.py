"""Add hr_documents.visibility for RBAC-aware retrieval.

Revision ID: 20261114_0010
Revises: 20261114_0009
Create Date: 2026-11-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0010"
down_revision: Union[str, Sequence[str], None] = "20261114_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

hr_document_visibility = sa.Enum("hr_only", "all_staff", name="hr_document_visibility")


def upgrade() -> None:
    hr_document_visibility.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "hr_documents",
        sa.Column(
            "visibility",
            hr_document_visibility,
            nullable=False,
            server_default="hr_only",
        ),
    )
    op.create_index("ix_hr_documents_visibility", "hr_documents", ["visibility"])


def downgrade() -> None:
    op.drop_index("ix_hr_documents_visibility", table_name="hr_documents")
    op.drop_column("hr_documents", "visibility")
    hr_document_visibility.drop(op.get_bind(), checkfirst=True)