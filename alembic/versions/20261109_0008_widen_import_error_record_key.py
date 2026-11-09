"""Widen import_errors.record_key to fit enrolment composite keys.

Revision ID: 20261109_0008
Revises: 20261109_0007
Create Date: 2026-11-09

employee_number (50) + '|' (1) + course_code (50) = 101 characters.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20261109_0008"
down_revision: Union[str, Sequence[str], None] = "20261109_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "import_errors",
        "record_key",
        existing_type=sa.String(length=100),
        type_=sa.String(length=101),
        existing_nullable=True,
    )


def count_overlength_record_keys(connection: Connection) -> int:
    """Return how many non-null record_key values exceed 100 characters."""
    result = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM import_errors "
            "WHERE record_key IS NOT NULL AND CHAR_LENGTH(record_key) > 100"
        )
    )
    return int(result.scalar_one())


def assert_safe_to_narrow_record_key(connection: Connection) -> None:
    """
    Refuse narrowing when any record_key would not fit VARCHAR(100).

    Downgrade must not truncate or collide composite identifiers.
    """
    count = count_overlength_record_keys(connection)
    if count > 0:
        raise RuntimeError(
            "Cannot downgrade revision 20261109_0008: "
            f"{count} import_errors.record_key value(s) exceed 100 characters. "
            "Shorten or remove those rows before narrowing the column; "
            "downgrade will not truncate data."
        )


def downgrade() -> None:
    assert_safe_to_narrow_record_key(op.get_bind())
    op.alter_column(
        "import_errors",
        "record_key",
        existing_type=sa.String(length=101),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
