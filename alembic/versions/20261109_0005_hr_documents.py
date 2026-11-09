"""Add hr_documents and hr_document_chunks for RAG metadata.

Revision ID: 20261109_0005
Revises: 20261101_0004
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_0005"
down_revision: Union[str, Sequence[str], None] = "20261101_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

hr_document_type = sa.Enum("policy", "handbook", "other", name="hr_document_type")
hr_document_status = sa.Enum(
    "pending",
    "processing",
    "indexed",
    "failed",
    name="hr_document_status",
)


def upgrade() -> None:
    hr_document_type.create(op.get_bind(), checkfirst=True)
    hr_document_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "hr_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("doc_type", hr_document_type, nullable=False),
        sa.Column(
            "status",
            hr_document_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_hr_documents_checksum", "hr_documents", ["checksum"])

    op.create_table(
        "hr_document_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section_title", sa.String(length=255), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["hr_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_hr_document_chunks_document_id_chunk_index",
        ),
    )


def downgrade() -> None:
    op.drop_table("hr_document_chunks")
    op.drop_index("ix_hr_documents_checksum", table_name="hr_documents")
    op.drop_table("hr_documents")
    hr_document_status.drop(op.get_bind(), checkfirst=True)
    hr_document_type.drop(op.get_bind(), checkfirst=True)
