"""Department ORM model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from smarthris.db.base import Base

if TYPE_CHECKING:
    from smarthris.models.employee import Employee


class Department(Base):
    """Organizational unit; employees belong to one department."""

    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    department_name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    employees: Mapped[list[Employee]] = relationship(back_populates="department")
