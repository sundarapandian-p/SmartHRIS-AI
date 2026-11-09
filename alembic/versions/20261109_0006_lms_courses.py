"""Add courses and course_enrolments for LMS analytics.

Revision ID: 20261109_0006
Revises: 20261109_0005
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_0006"
down_revision: Union[str, Sequence[str], None] = "20261109_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

course_status = sa.Enum("active", "inactive", name="course_status")
course_enrolment_status = sa.Enum(
    "enrolled",
    "in_progress",
    "completed",
    "cancelled",
    name="course_enrolment_status",
)


def upgrade() -> None:
    course_status.create(op.get_bind(), checkfirst=True)
    course_enrolment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "courses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("course_code", sa.String(length=50), nullable=False),
        sa.Column("course_name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column(
            "status",
            course_status,
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_code"),
    )
    op.create_index("ix_courses_course_code", "courses", ["course_code"])
    op.create_index("ix_courses_category", "courses", ["category"])

    op.create_table(
        "course_enrolments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("enrolment_date", sa.Date(), nullable=False),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column(
            "status",
            course_enrolment_status,
            nullable=False,
            server_default="enrolled",
        ),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
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
        sa.CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 100)",
            name="ck_course_enrolments_score_range",
        ),
        sa.CheckConstraint(
            "completion_date IS NULL OR completion_date >= enrolment_date",
            name="ck_course_enrolments_completion_after_enrolment",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR completion_date IS NOT NULL",
            name="ck_course_enrolments_completed_requires_date",
        ),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "employee_id",
            "course_id",
            name="uq_course_enrolments_employee_id_course_id",
        ),
    )
    op.create_index("ix_course_enrolments_employee_id", "course_enrolments", ["employee_id"])
    op.create_index("ix_course_enrolments_course_id", "course_enrolments", ["course_id"])
    op.create_index("ix_course_enrolments_status", "course_enrolments", ["status"])
    op.create_index(
        "ix_course_enrolments_course_id_status",
        "course_enrolments",
        ["course_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_course_enrolments_course_id_status", table_name="course_enrolments")
    op.drop_index("ix_course_enrolments_status", table_name="course_enrolments")
    op.drop_index("ix_course_enrolments_course_id", table_name="course_enrolments")
    op.drop_index("ix_course_enrolments_employee_id", table_name="course_enrolments")
    op.drop_table("course_enrolments")
    op.drop_index("ix_courses_category", table_name="courses")
    op.drop_index("ix_courses_course_code", table_name="courses")
    op.drop_table("courses")
    course_enrolment_status.drop(op.get_bind(), checkfirst=True)
    course_status.drop(op.get_bind(), checkfirst=True)
