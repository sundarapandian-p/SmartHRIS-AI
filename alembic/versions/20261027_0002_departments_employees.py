"""Add departments and employees tables.

Revision ID: 20261027_0002
Revises: 20261026_0001
Create Date: 2026-10-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0002"
down_revision: Union[str, Sequence[str], None] = "20261026_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

employee_status = sa.Enum("active", "inactive", name="employee_status")


def upgrade() -> None:
    employee_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("department_name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("department_name"),
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_number", sa.String(length=50), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("work_email", sa.String(length=255), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("job_role", sa.String(length=100), nullable=True),
        sa.Column("job_title", sa.String(length=150), nullable=True),
        sa.Column("manager_employee_id", sa.Integer(), nullable=True),
        sa.Column("hire_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            employee_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.ForeignKeyConstraint(["manager_employee_id"], ["employees.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("employee_number"),
        sa.UniqueConstraint("work_email"),
    )
    op.create_index("ix_employees_department_id", "employees", ["department_id"])
    op.create_index("ix_employees_employee_number", "employees", ["employee_number"])
    op.create_index("ix_employees_manager_employee_id", "employees", ["manager_employee_id"])
    op.create_index("ix_employees_work_email", "employees", ["work_email"])


def downgrade() -> None:
    op.drop_index("ix_employees_work_email", table_name="employees")
    op.drop_index("ix_employees_manager_employee_id", table_name="employees")
    op.drop_index("ix_employees_employee_number", table_name="employees")
    op.drop_index("ix_employees_department_id", table_name="employees")
    op.drop_table("employees")
    op.drop_table("departments")
    employee_status.drop(op.get_bind(), checkfirst=True)
